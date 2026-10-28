/*
 * quicfile: minimal file download over msquic for the MCN26 experiments.
 *
 *   quicfile server -dir:<path> -cert:<file> -key:<file> [-port:4433]
 *   quicfile client -target:<ip> -file:<name> [-port:4433] [-out:<path>]
 *
 * Common options:
 *   -policy:default|fixed|litequic   ACK frequency policy. The data sender (server)
 *                                    is the side that applies it.
 *   -threshold:<N>                   ACK threshold for -policy:fixed (default 2)
 *   -cc:cubic|bbr                    congestion control (default cubic)
 *   -acklog:1                        print ACK threshold changes to stderr
 *
 * Protocol: the client opens one bidirectional stream and sends the file name
 * with FIN; the server replies with the file contents and FIN.
 *
 * The client prints one "RESULT key=value ..." line per download; the server
 * prints one "SERVER key=value ..." line per connection.
 */

#define _GNU_SOURCE
#define QUIC_API_ENABLE_PREVIEW_FEATURES 1 // For BBR
#include <msquic.h>

#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define DEFAULT_PORT 4433
#define CHUNK_SIZE (256 * 1024)
#define SEND_SLOTS 64 // 16 MiB queued per stream; covers ~1 Gbps x 100 ms
#define MAX_NAME 255

static const QUIC_API_TABLE* MsQuic;
static HQUIC Registration;
static HQUIC Configuration;
static const QUIC_BUFFER Alpn = { sizeof("mcn26") - 1, (uint8_t*)"mcn26" };
static const QUIC_REGISTRATION_CONFIG RegConfig = {
    "quicfile", QUIC_EXECUTION_PROFILE_TYPE_MAX_THROUGHPUT };

static const char*
GetArg(int argc, char** argv, const char* Name)
{
    size_t Len = strlen(Name);
    for (int i = 2; i < argc; ++i) {
        if (argv[i][0] == '-' && strncmp(argv[i] + 1, Name, Len) == 0 &&
            argv[i][1 + Len] == ':') {
            return argv[i] + 2 + Len;
        }
    }
    return NULL;
}

static double
NowMs(void)
{
    struct timespec Ts;
    clock_gettime(CLOCK_MONOTONIC, &Ts);
    return Ts.tv_sec * 1e3 + Ts.tv_nsec / 1e6;
}

static double
ProcessCpuMs(void)
{
    struct rusage Usage;
    getrusage(RUSAGE_SELF, &Usage);
    return (Usage.ru_utime.tv_sec + Usage.ru_stime.tv_sec) * 1e3 +
           (Usage.ru_utime.tv_usec + Usage.ru_stime.tv_usec) / 1e3;
}

//
// Applies -policy/-threshold/-acklog. msquic reads these environment variables
// (see QuicMcnAckPolicy in msquic/src/core/connection.c), so this must run
// before MsQuicOpen2.
//
static int
ApplyAckPolicy(int argc, char** argv)
{
    const char* Policy = GetArg(argc, argv, "policy");
    if (Policy == NULL) {
        Policy = "default";
    }
    if (strcmp(Policy, "default") != 0 && strcmp(Policy, "fixed") != 0 &&
        strcmp(Policy, "litequic") != 0) {
        fprintf(stderr, "Unknown -policy:%s (default|fixed|litequic)\n", Policy);
        return -1;
    }
    setenv("MCN_ACK_POLICY", Policy, 1);

    const char* Threshold = GetArg(argc, argv, "threshold");
    if (Threshold != NULL) {
        setenv("MCN_ACK_THRESHOLD", Threshold, 1);
    }
    const char* Log = GetArg(argc, argv, "acklog");
    if (Log != NULL) {
        setenv("MCN_ACK_LOG", Log, 1);
    }
    return 0;
}

static void
ApplyCommonSettings(int argc, char** argv, QUIC_SETTINGS* Settings)
{
    Settings->IdleTimeoutMs = 30000;
    Settings->IsSet.IdleTimeoutMs = TRUE;

    const char* Cc = GetArg(argc, argv, "cc");
    Settings->CongestionControlAlgorithm =
        (Cc != NULL && strcmp(Cc, "bbr") == 0) ?
            QUIC_CONGESTION_CONTROL_ALGORITHM_BBR :
            QUIC_CONGESTION_CONTROL_ALGORITHM_CUBIC;
    Settings->IsSet.CongestionControlAlgorithm = TRUE;
}

static void
PrintStats(const char* Prefix, HQUIC Connection)
{
    QUIC_STATISTICS_V2 Stats;
    uint32_t Size = sizeof(Stats);
    if (QUIC_FAILED(MsQuic->GetParam(
            Connection, QUIC_PARAM_CONN_STATISTICS_V2, &Size, &Stats))) {
        return;
    }
    printf(
        "%s_sent_pkts=%llu %s_recv_pkts=%llu %s_recv_ack_frames=%llu "
        "%s_lost_pkts=%llu %s_congestion_events=%u %s_rtt_us=%u %s_min_rtt_us=%u",
        Prefix, (unsigned long long)Stats.SendTotalPackets,
        Prefix, (unsigned long long)Stats.RecvTotalPackets,
        Prefix, (unsigned long long)Stats.RecvValidAckFrames,
        Prefix, (unsigned long long)Stats.SendSuspectedLostPackets,
        Prefix, Stats.SendCongestionCount,
        Prefix, Stats.Rtt,
        Prefix, Stats.MinRtt);
}

//
// Server
//

typedef struct SERVER_STREAM {
    HQUIC Stream;
    char Name[MAX_NAME + 1];
    size_t NameLength;
    int Fd;
    uint64_t Size;
    uint64_t Offset;
    QUIC_BUFFER Buffers[SEND_SLOTS];
} SERVER_STREAM;

typedef struct SERVER_CONN {
    double StartCpuMs;
    double StartMs;
} SERVER_CONN;

static const char* ServeDir;

static void
ServerSendNext(SERVER_STREAM* Ctx, uintptr_t Slot)
{
    if (Ctx->Offset >= Ctx->Size) {
        return;
    }
    ssize_t Read = pread(Ctx->Fd, Ctx->Buffers[Slot].Buffer, CHUNK_SIZE, (off_t)Ctx->Offset);
    if (Read <= 0) {
        MsQuic->StreamShutdown(Ctx->Stream, QUIC_STREAM_SHUTDOWN_FLAG_ABORT, 2);
        return;
    }
    Ctx->Offset += (uint64_t)Read;
    Ctx->Buffers[Slot].Length = (uint32_t)Read;
    QUIC_SEND_FLAGS Flags =
        Ctx->Offset >= Ctx->Size ? QUIC_SEND_FLAG_FIN : QUIC_SEND_FLAG_NONE;
    if (QUIC_FAILED(MsQuic->StreamSend(
            Ctx->Stream, &Ctx->Buffers[Slot], 1, Flags, (void*)Slot))) {
        MsQuic->StreamShutdown(Ctx->Stream, QUIC_STREAM_SHUTDOWN_FLAG_ABORT, 2);
    }
}

static void
ServerStartFile(SERVER_STREAM* Ctx)
{
    char Path[4096];
    struct stat St;
    Ctx->Name[Ctx->NameLength] = '\0';
    if (Ctx->NameLength == 0 || strchr(Ctx->Name, '/') != NULL ||
        strcmp(Ctx->Name, "..") == 0 ||
        snprintf(Path, sizeof(Path), "%s/%s", ServeDir, Ctx->Name) >= (int)sizeof(Path) ||
        (Ctx->Fd = open(Path, O_RDONLY)) < 0 ||
        fstat(Ctx->Fd, &St) != 0) {
        fprintf(stderr, "Rejected request '%s'\n", Ctx->Name);
        MsQuic->StreamShutdown(Ctx->Stream, QUIC_STREAM_SHUTDOWN_FLAG_ABORT, 1);
        return;
    }
    Ctx->Size = (uint64_t)St.st_size;
    if (Ctx->Size == 0) {
        MsQuic->StreamShutdown(Ctx->Stream, QUIC_STREAM_SHUTDOWN_FLAG_GRACEFUL, 0);
        return;
    }
    for (uintptr_t i = 0; i < SEND_SLOTS; ++i) {
        ServerSendNext(Ctx, i);
    }
}

static QUIC_STATUS QUIC_API
ServerStreamCallback(HQUIC Stream, void* Context, QUIC_STREAM_EVENT* Event)
{
    SERVER_STREAM* Ctx = (SERVER_STREAM*)Context;
    switch (Event->Type) {
    case QUIC_STREAM_EVENT_RECEIVE:
        for (uint32_t i = 0; i < Event->RECEIVE.BufferCount; ++i) {
            const QUIC_BUFFER* B = &Event->RECEIVE.Buffers[i];
            size_t Copy = B->Length;
            if (Copy > MAX_NAME - Ctx->NameLength) {
                Copy = MAX_NAME - Ctx->NameLength;
            }
            memcpy(Ctx->Name + Ctx->NameLength, B->Buffer, Copy);
            Ctx->NameLength += Copy;
        }
        break;
    case QUIC_STREAM_EVENT_PEER_SEND_SHUTDOWN:
        ServerStartFile(Ctx);
        break;
    case QUIC_STREAM_EVENT_SEND_COMPLETE:
        if (!Event->SEND_COMPLETE.Canceled) {
            ServerSendNext(Ctx, (uintptr_t)Event->SEND_COMPLETE.ClientContext);
        }
        break;
    case QUIC_STREAM_EVENT_PEER_SEND_ABORTED:
    case QUIC_STREAM_EVENT_PEER_RECEIVE_ABORTED:
        MsQuic->StreamShutdown(Stream, QUIC_STREAM_SHUTDOWN_FLAG_ABORT, 0);
        break;
    case QUIC_STREAM_EVENT_SHUTDOWN_COMPLETE:
        if (Ctx->Fd >= 0) {
            close(Ctx->Fd);
        }
        for (int i = 0; i < SEND_SLOTS; ++i) {
            free(Ctx->Buffers[i].Buffer);
        }
        free(Ctx);
        MsQuic->StreamClose(Stream);
        break;
    default:
        break;
    }
    return QUIC_STATUS_SUCCESS;
}

static QUIC_STATUS QUIC_API
ServerConnectionCallback(HQUIC Connection, void* Context, QUIC_CONNECTION_EVENT* Event)
{
    SERVER_CONN* Conn = (SERVER_CONN*)Context;
    switch (Event->Type) {
    case QUIC_CONNECTION_EVENT_PEER_STREAM_STARTED: {
        SERVER_STREAM* Ctx = calloc(1, sizeof(*Ctx));
        if (Ctx == NULL) {
            return QUIC_STATUS_OUT_OF_MEMORY;
        }
        Ctx->Stream = Event->PEER_STREAM_STARTED.Stream;
        Ctx->Fd = -1;
        // Buffers left NULL on allocation failure are freed (free(NULL)) on shutdown.
        MsQuic->SetCallbackHandler(Ctx->Stream, (void*)ServerStreamCallback, Ctx);
        for (int i = 0; i < SEND_SLOTS; ++i) {
            Ctx->Buffers[i].Buffer = malloc(CHUNK_SIZE);
            if (Ctx->Buffers[i].Buffer == NULL) {
                MsQuic->StreamShutdown(Ctx->Stream, QUIC_STREAM_SHUTDOWN_FLAG_ABORT, 3);
                break;
            }
        }
        break;
    }
    case QUIC_CONNECTION_EVENT_SHUTDOWN_INITIATED_BY_TRANSPORT:
    case QUIC_CONNECTION_EVENT_SHUTDOWN_INITIATED_BY_PEER:
        break;
    case QUIC_CONNECTION_EVENT_SHUTDOWN_COMPLETE:
        printf("SERVER policy=%s duration_ms=%.1f cpu_ms=%.1f ",
               getenv("MCN_ACK_POLICY"),
               NowMs() - Conn->StartMs,
               ProcessCpuMs() - Conn->StartCpuMs);
        PrintStats("server", Connection);
        printf("\n");
        fflush(stdout);
        free(Conn);
        MsQuic->ConnectionClose(Connection);
        break;
    default:
        break;
    }
    return QUIC_STATUS_SUCCESS;
}

static QUIC_STATUS QUIC_API
ServerListenerCallback(HQUIC Listener, void* Context, QUIC_LISTENER_EVENT* Event)
{
    (void)Listener;
    (void)Context;
    if (Event->Type != QUIC_LISTENER_EVENT_NEW_CONNECTION) {
        return QUIC_STATUS_SUCCESS;
    }
    SERVER_CONN* Conn = calloc(1, sizeof(*Conn));
    if (Conn == NULL) {
        return QUIC_STATUS_OUT_OF_MEMORY;
    }
    Conn->StartMs = NowMs();
    Conn->StartCpuMs = ProcessCpuMs();
    HQUIC Connection = Event->NEW_CONNECTION.Connection;
    MsQuic->SetCallbackHandler(Connection, (void*)ServerConnectionCallback, Conn);
    return MsQuic->ConnectionSetConfiguration(Connection, Configuration);
}

static int
RunServer(int argc, char** argv)
{
    ServeDir = GetArg(argc, argv, "dir");
    const char* Cert = GetArg(argc, argv, "cert");
    const char* Key = GetArg(argc, argv, "key");
    const char* PortArg = GetArg(argc, argv, "port");
    if (ServeDir == NULL || Cert == NULL || Key == NULL) {
        fprintf(stderr, "server needs -dir, -cert and -key\n");
        return 1;
    }

    QUIC_SETTINGS Settings = {0};
    ApplyCommonSettings(argc, argv, &Settings);
    Settings.PeerBidiStreamCount = 16;
    Settings.IsSet.PeerBidiStreamCount = TRUE;
    Settings.SendBufferingEnabled = FALSE; // We keep our own buffers queued.
    Settings.IsSet.SendBufferingEnabled = TRUE;

    QUIC_CERTIFICATE_FILE CertFile = { Key, Cert };
    QUIC_CREDENTIAL_CONFIG Cred = {0};
    Cred.Type = QUIC_CREDENTIAL_TYPE_CERTIFICATE_FILE;
    Cred.CertificateFile = &CertFile;

    QUIC_STATUS Status;
    if (QUIC_FAILED(Status = MsQuic->ConfigurationOpen(
            Registration, &Alpn, 1, &Settings, sizeof(Settings), NULL, &Configuration)) ||
        QUIC_FAILED(Status = MsQuic->ConfigurationLoadCredential(Configuration, &Cred))) {
        fprintf(stderr, "Server configuration failed, 0x%x\n", Status);
        return 1;
    }

    HQUIC Listener;
    QUIC_ADDR Address = {0};
    QuicAddrSetFamily(&Address, QUIC_ADDRESS_FAMILY_UNSPEC);
    QuicAddrSetPort(&Address, PortArg ? (uint16_t)atoi(PortArg) : DEFAULT_PORT);
    if (QUIC_FAILED(Status = MsQuic->ListenerOpen(
            Registration, ServerListenerCallback, NULL, &Listener)) ||
        QUIC_FAILED(Status = MsQuic->ListenerStart(Listener, &Alpn, 1, &Address))) {
        fprintf(stderr, "Listener failed, 0x%x\n", Status);
        return 1;
    }
    printf("Serving %s on port %u (policy=%s)\n",
           ServeDir, QuicAddrGetPort(&Address), getenv("MCN_ACK_POLICY"));
    fflush(stdout);

    //
    // SIGINT/SIGTERM were blocked before MsQuicOpen2, so all msquic threads
    // inherit the mask and the signal is delivered here.
    //
    sigset_t Signals;
    int Signal;
    sigemptyset(&Signals);
    sigaddset(&Signals, SIGINT);
    sigaddset(&Signals, SIGTERM);
    sigwait(&Signals, &Signal);

    MsQuic->ListenerClose(Listener);
    return 0;
}

//
// Client
//

typedef struct CLIENT {
    const char* File;
    int OutFd;
    uint64_t Bytes;
    double StartMs;
    double ConnectedMs;
    double RequestMs;
    double DoneMs;
    double StartCpuMs;
    int Succeeded;
    int Done;     // Download finished or failed.
    int Finished; // Connection shutdown complete.
    pthread_mutex_t Lock;
    pthread_cond_t Cond;
} CLIENT;

static void
ClientSignal(CLIENT* Client, int* Flag)
{
    pthread_mutex_lock(&Client->Lock);
    *Flag = 1;
    pthread_cond_signal(&Client->Cond);
    pthread_mutex_unlock(&Client->Lock);
}

static void
ClientWait(CLIENT* Client, const int* Flag)
{
    pthread_mutex_lock(&Client->Lock);
    while (!*Flag) {
        pthread_cond_wait(&Client->Cond, &Client->Lock);
    }
    pthread_mutex_unlock(&Client->Lock);
}

static QUIC_STATUS QUIC_API
ClientStreamCallback(HQUIC Stream, void* Context, QUIC_STREAM_EVENT* Event)
{
    CLIENT* Client = (CLIENT*)Context;
    switch (Event->Type) {
    case QUIC_STREAM_EVENT_RECEIVE:
        for (uint32_t i = 0; i < Event->RECEIVE.BufferCount; ++i) {
            const QUIC_BUFFER* B = &Event->RECEIVE.Buffers[i];
            if (Client->OutFd >= 0 &&
                write(Client->OutFd, B->Buffer, B->Length) != (ssize_t)B->Length) {
                fprintf(stderr, "write failed: %s\n", strerror(errno));
            }
        }
        Client->Bytes += Event->RECEIVE.TotalBufferLength;
        break;
    case QUIC_STREAM_EVENT_PEER_SEND_SHUTDOWN:
        Client->DoneMs = NowMs();
        Client->Succeeded = 1;
        ClientSignal(Client, &Client->Done);
        break;
    case QUIC_STREAM_EVENT_PEER_SEND_ABORTED:
        fprintf(stderr, "Server aborted the download (error %llu)\n",
                (unsigned long long)Event->PEER_SEND_ABORTED.ErrorCode);
        ClientSignal(Client, &Client->Done);
        break;
    case QUIC_STREAM_EVENT_SHUTDOWN_COMPLETE:
        MsQuic->StreamClose(Stream);
        break;
    default:
        break;
    }
    return QUIC_STATUS_SUCCESS;
}

static QUIC_STATUS QUIC_API
ClientConnectionCallback(HQUIC Connection, void* Context, QUIC_CONNECTION_EVENT* Event)
{
    CLIENT* Client = (CLIENT*)Context;
    switch (Event->Type) {
    case QUIC_CONNECTION_EVENT_CONNECTED: {
        Client->ConnectedMs = NowMs();
        HQUIC Stream;
        static QUIC_BUFFER Request;
        Request.Buffer = (uint8_t*)Client->File;
        Request.Length = (uint32_t)strlen(Client->File);
        if (QUIC_FAILED(MsQuic->StreamOpen(
                Connection, QUIC_STREAM_OPEN_FLAG_NONE, ClientStreamCallback, Client, &Stream)) ||
            QUIC_FAILED(MsQuic->StreamStart(Stream, QUIC_STREAM_START_FLAG_NONE))) {
            MsQuic->ConnectionShutdown(Connection, QUIC_CONNECTION_SHUTDOWN_FLAG_NONE, 1);
            break;
        }
        Client->RequestMs = NowMs();
        if (QUIC_FAILED(MsQuic->StreamSend(Stream, &Request, 1, QUIC_SEND_FLAG_FIN, NULL))) {
            MsQuic->ConnectionShutdown(Connection, QUIC_CONNECTION_SHUTDOWN_FLAG_NONE, 1);
        }
        break;
    }
    case QUIC_CONNECTION_EVENT_SHUTDOWN_INITIATED_BY_TRANSPORT:
        fprintf(stderr, "Connection shut down by transport, 0x%x\n",
                Event->SHUTDOWN_INITIATED_BY_TRANSPORT.Status);
        break;
    case QUIC_CONNECTION_EVENT_SHUTDOWN_COMPLETE:
        ClientSignal(Client, &Client->Done);
        ClientSignal(Client, &Client->Finished);
        break;
    default:
        break;
    }
    return QUIC_STATUS_SUCCESS;
}

static int
RunClient(int argc, char** argv)
{
    const char* Target = GetArg(argc, argv, "target");
    const char* PortArg = GetArg(argc, argv, "port");
    const char* Out = GetArg(argc, argv, "out");
    CLIENT Client = {0};
    Client.File = GetArg(argc, argv, "file");
    Client.OutFd = -1;
    if (Target == NULL || Client.File == NULL) {
        fprintf(stderr, "client needs -target and -file\n");
        return 1;
    }
    if (Out != NULL &&
        (Client.OutFd = open(Out, O_WRONLY | O_CREAT | O_TRUNC, 0644)) < 0) {
        fprintf(stderr, "Cannot open %s: %s\n", Out, strerror(errno));
        return 1;
    }
    pthread_mutex_init(&Client.Lock, NULL);
    pthread_cond_init(&Client.Cond, NULL);

    QUIC_SETTINGS Settings = {0};
    ApplyCommonSettings(argc, argv, &Settings);
    // Large receive windows so flow control never limits the download.
    Settings.ConnFlowControlWindow = 256 * 1024 * 1024;
    Settings.IsSet.ConnFlowControlWindow = TRUE;
    Settings.StreamRecvWindowDefault = 64 * 1024 * 1024;
    Settings.IsSet.StreamRecvWindowDefault = TRUE;
    Settings.StreamRecvWindowBidiLocalDefault = 64 * 1024 * 1024;
    Settings.IsSet.StreamRecvWindowBidiLocalDefault = TRUE;

    QUIC_CREDENTIAL_CONFIG Cred = {0};
    Cred.Type = QUIC_CREDENTIAL_TYPE_NONE;
    Cred.Flags = QUIC_CREDENTIAL_FLAG_CLIENT | QUIC_CREDENTIAL_FLAG_NO_CERTIFICATE_VALIDATION;

    QUIC_STATUS Status;
    HQUIC Connection;
    if (QUIC_FAILED(Status = MsQuic->ConfigurationOpen(
            Registration, &Alpn, 1, &Settings, sizeof(Settings), NULL, &Configuration)) ||
        QUIC_FAILED(Status = MsQuic->ConfigurationLoadCredential(Configuration, &Cred)) ||
        QUIC_FAILED(Status = MsQuic->ConnectionOpen(
            Registration, ClientConnectionCallback, &Client, &Connection))) {
        fprintf(stderr, "Client setup failed, 0x%x\n", Status);
        return 1;
    }

    Client.StartMs = NowMs();
    Client.StartCpuMs = ProcessCpuMs();
    if (QUIC_FAILED(Status = MsQuic->ConnectionStart(
            Connection, Configuration, QUIC_ADDRESS_FAMILY_UNSPEC, Target,
            PortArg ? (uint16_t)atoi(PortArg) : DEFAULT_PORT))) {
        fprintf(stderr, "ConnectionStart failed, 0x%x\n", Status);
        MsQuic->ConnectionClose(Connection);
        return 1;
    }

    //
    // Wait for the download (or a failure), then report and close.
    //
    ClientWait(&Client, &Client.Done);
    double CpuMs = ProcessCpuMs() - Client.StartCpuMs;

    if (Client.Succeeded) {
        double TransferMs = Client.DoneMs - Client.RequestMs;
        printf("RESULT file=%s bytes=%llu policy=%s handshake_ms=%.2f transfer_ms=%.2f "
               "goodput_mbps=%.1f cpu_ms=%.1f ",
               Client.File, (unsigned long long)Client.Bytes, getenv("MCN_ACK_POLICY"),
               Client.ConnectedMs - Client.StartMs, TransferMs,
               Client.Bytes * 8 / (TransferMs * 1e3), CpuMs);
        PrintStats("client", Connection);
        printf("\n");
        fflush(stdout);
    }

    MsQuic->ConnectionShutdown(Connection, QUIC_CONNECTION_SHUTDOWN_FLAG_NONE, 0);
    ClientWait(&Client, &Client.Finished);
    MsQuic->ConnectionClose(Connection);
    if (Client.OutFd >= 0) {
        close(Client.OutFd);
    }
    return Client.Succeeded ? 0 : 1;
}

int
main(int argc, char** argv)
{
    if (argc < 2 || (strcmp(argv[1], "server") != 0 && strcmp(argv[1], "client") != 0)) {
        fprintf(stderr,
            "usage: %s server -dir:<path> -cert:<file> -key:<file> [-port:N] [options]\n"
            "       %s client -target:<ip> -file:<name> [-port:N] [-out:<path>] [options]\n"
            "options: -policy:default|fixed|litequic -threshold:N -cc:cubic|bbr -acklog:1\n",
            argv[0], argv[0]);
        return 1;
    }
    if (ApplyAckPolicy(argc, argv) != 0) {
        return 1;
    }

    sigset_t Signals;
    sigemptyset(&Signals);
    sigaddset(&Signals, SIGINT);
    sigaddset(&Signals, SIGTERM);
    pthread_sigmask(SIG_BLOCK, &Signals, NULL);
    if (strcmp(argv[1], "client") == 0) {
        pthread_sigmask(SIG_UNBLOCK, &Signals, NULL); // Ctrl-C just exits the client.
    }

    QUIC_STATUS Status;
    if (QUIC_FAILED(Status = MsQuicOpen2(&MsQuic))) {
        fprintf(stderr, "MsQuicOpen2 failed, 0x%x\n", Status);
        return 1;
    }
    if (QUIC_FAILED(Status = MsQuic->RegistrationOpen(&RegConfig, &Registration))) {
        fprintf(stderr, "RegistrationOpen failed, 0x%x\n", Status);
        MsQuicClose(MsQuic);
        return 1;
    }

    int Result = strcmp(argv[1], "server") == 0 ? RunServer(argc, argv) : RunClient(argc, argv);

    if (Configuration != NULL) {
        MsQuic->ConfigurationClose(Configuration);
    }
    MsQuic->RegistrationClose(Registration);
    MsQuicClose(MsQuic);
    return Result;
}
