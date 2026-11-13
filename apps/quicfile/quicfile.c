/*
 * quicfile: minimal file download over msquic for the MCN26 experiments.
 *
 *   quicfile server -dir:<path> -cert:<file> -key:<file> [-port:4433]
 *   quicfile client -target:<ip> -file:<name> [-port:4433] [-out:<path>] [-tag:<id>]
 *   quicfile client -target:<ip> -video:1 [-ladder:25,50,...] [-segments:20] [-segdur:2]
 *                   [-bufmax:10] [-tag:<id>]
 *
 * Common options:
 *   -policy:default|fixed|litequic|adaptive
 *                                    ACK frequency policy. The data sender (server)
 *                                    is the side that applies it.
 *   -threshold:<N>                   ACK threshold for -policy:fixed (default 2)
 *   -acknet:0|1                      adaptive: also use network conditions (default 0)
 *   -ackmax:<N>                      adaptive: highest threshold (default 100, at most 255)
 *   -ackphase:1                      modifier: slow start T <= cwnd/4, recovery T = 2
 *   -ackreorder:<N>                  modifier: request reordering threshold N (default 1)
 *   -ackdelay:1                      modifier: request max ACK delay SRTT/4 (1-25 ms)
 *   -cc:cubic|bbr                    congestion control (default cubic)
 *   -acklog:1                        print ACK policy decisions and per-connection
 *                                    ACK-cost statistics ([mcn-stats]) to stderr
 *
 * Protocol: per request the client opens a bidirectional stream and sends
 * "<file> <tag>" with FIN; the server replies with the file contents and FIN.
 * A download is one request; a video session requests one segment file per
 * stream, all on one connection.
 *
 * The client prints one "RESULT key=value ..." line per download (plus a "TPUT"
 * line: throughput in every 100 ms of the transfer) or, in video mode, one
 * "SEG" line per segment and one "VIDEO" line with the QoE metrics (see
 * ClientVideo). The server prints one "SERVER key=value ..." line per
 * connection. All carry the tag, so they can be joined. Client timings:
 * download_ms = connection start to last byte (what a user waits for),
 * handshake_ms, ttfb_ms = request to first byte, transfer_ms = request to last
 * byte, goodput_mbps = bytes / transfer time, tput_p10/p50/p90_mbps =
 * percentiles of the 100 ms throughput samples. sys_cpu_pct is whole-machine
 * CPU utilisation over the transfer (from /proc/stat); cpu_ms is this
 * process's CPU time.
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
// Whole-machine CPU counters from the first line of /proc/stat (in ticks).
//
typedef struct SYS_CPU {
    unsigned long long Busy;
    unsigned long long Total;
} SYS_CPU;

static SYS_CPU
SystemCpu(void)
{
    SYS_CPU Cpu = {0, 0};
    unsigned long long V[8] = {0};
    FILE* F = fopen("/proc/stat", "r");
    if (F != NULL) {
        if (fscanf(F, "cpu %llu %llu %llu %llu %llu %llu %llu %llu",
                   &V[0], &V[1], &V[2], &V[3], &V[4], &V[5], &V[6], &V[7]) == 8) {
            for (int i = 0; i < 8; ++i) {
                Cpu.Total += V[i];
            }
            Cpu.Busy = Cpu.Total - V[3] - V[4]; // minus idle and iowait
        }
        fclose(F);
    }
    return Cpu;
}

static double
SystemCpuPct(SYS_CPU Start)
{
    SYS_CPU End = SystemCpu();
    return End.Total > Start.Total ?
        100.0 * (End.Busy - Start.Busy) / (End.Total - Start.Total) : 0;
}

//
// Applies -policy/-threshold/-acknet/-ackmax/-acklog. msquic reads these environment variables
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
        strcmp(Policy, "litequic") != 0 && strcmp(Policy, "adaptive") != 0) {
        fprintf(stderr, "Unknown -policy:%s (default|fixed|litequic|adaptive)\n", Policy);
        return -1;
    }
    setenv("MCN_ACK_POLICY", Policy, 1);

    static const struct { const char* Arg; const char* Env; } Passthrough[] = {
        { "threshold", "MCN_ACK_THRESHOLD" },
        { "acknet", "MCN_ACK_NET" },
        { "ackmax", "MCN_ACK_MAX" },
        { "ackphase", "MCN_ACK_PHASE" },
        { "ackreorder", "MCN_ACK_REORDER" },
        { "ackdelay", "MCN_ACK_DELAY" },
        { "acklog", "MCN_ACK_LOG" },
    };
    for (size_t i = 0; i < sizeof(Passthrough) / sizeof(Passthrough[0]); ++i) {
        const char* Value = GetArg(argc, argv, Passthrough[i].Arg);
        if (Value != NULL) {
            setenv(Passthrough[i].Env, Value, 1);
        }
    }
    if (getenv("MCN_ACK_NET") == NULL) {
        setenv("MCN_ACK_NET", "0", 1);
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
        "%s_lost_pkts=%llu %s_congestion_events=%u %s_rtt_us=%u %s_min_rtt_us=%u "
        "%s_reordered_pkts=%llu",
        Prefix, (unsigned long long)Stats.SendTotalPackets,
        Prefix, (unsigned long long)Stats.RecvTotalPackets,
        Prefix, (unsigned long long)Stats.RecvValidAckFrames,
        Prefix, (unsigned long long)Stats.SendSuspectedLostPackets,
        Prefix, Stats.SendCongestionCount,
        Prefix, Stats.Rtt,
        Prefix, Stats.MinRtt,
        Prefix, (unsigned long long)Stats.RecvReorderedPackets);
}

//
// Server
//

typedef struct SERVER_CONN {
    double StartCpuMs;
    double StartMs;
    SYS_CPU StartSys;
    char File[MAX_NAME + 1];
    char Tag[MAX_NAME + 1];
    uint64_t Bytes;   // Over all streams of the connection.
    uint32_t Streams; // Requests served.
} SERVER_CONN;

typedef struct SERVER_STREAM {
    HQUIC Stream;
    SERVER_CONN* Conn;
    char Name[MAX_NAME + 1]; // Request: "<file> <tag>"
    size_t NameLength;
    int Fd;
    uint64_t Size;
    uint64_t Offset;
    QUIC_BUFFER Buffers[SEND_SLOTS];
} SERVER_STREAM;

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
    char* Tag = strchr(Ctx->Name, ' ');
    if (Tag != NULL) {
        *Tag++ = '\0';
        snprintf(Ctx->Conn->Tag, sizeof(Ctx->Conn->Tag), "%s", Tag);
    }
    snprintf(Ctx->Conn->File, sizeof(Ctx->Conn->File), "%s", Ctx->Name);
    if (Ctx->Name[0] == '\0' || strchr(Ctx->Name, '/') != NULL ||
        strcmp(Ctx->Name, "..") == 0 ||
        snprintf(Path, sizeof(Path), "%s/%s", ServeDir, Ctx->Name) >= (int)sizeof(Path) ||
        (Ctx->Fd = open(Path, O_RDONLY)) < 0 ||
        fstat(Ctx->Fd, &St) != 0) {
        fprintf(stderr, "Rejected request '%s'\n", Ctx->Name);
        MsQuic->StreamShutdown(Ctx->Stream, QUIC_STREAM_SHUTDOWN_FLAG_ABORT, 1);
        return;
    }
    Ctx->Size = (uint64_t)St.st_size;
    Ctx->Conn->Bytes += Ctx->Size;
    Ctx->Conn->Streams++;
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
        Ctx->Conn = Conn;
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
        printf("SERVER tag=%s file=%s bytes=%llu streams=%u policy=%s acknet=%s conn=%p "
               "duration_ms=%.1f cpu_ms=%.1f sys_cpu_pct=%.1f ",
               Conn->Tag[0] ? Conn->Tag : "-",
               Conn->File[0] ? Conn->File : "-",
               (unsigned long long)Conn->Bytes, Conn->Streams,
               getenv("MCN_ACK_POLICY"),
               getenv("MCN_ACK_NET"),
               (void*)Connection,
               NowMs() - Conn->StartMs,
               ProcessCpuMs() - Conn->StartCpuMs,
               SystemCpuPct(Conn->StartSys));
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
    Conn->StartSys = SystemCpu();
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

#define TPUT_BIN_MS 100
#define TPUT_MAX_BINS 12000 // 20 minutes of 100 ms bins
#define MAX_LADDER 16

typedef struct CLIENT {
    const char* Tag;
    SYS_CPU StartSys;
    int OutFd;
    uint64_t Bytes;       // Over the whole connection.
    uint64_t StreamBytes; // Of the current request.
    double StartMs;
    double ConnectedMs;
    double RequestMs;
    double FirstByteMs;
    double DoneMs;
    double StartCpuMs;
    uint64_t* Bins; // Bytes received per TPUT_BIN_MS since RequestMs (file mode).
    uint32_t Seq;   // Current request; callbacks of older streams are ignored.
    int Connected;
    int StreamDone;
    int StreamOk;
    int Finished;   // Connection shutdown complete.
    pthread_mutex_t Lock;
    pthread_cond_t Cond;
} CLIENT;

typedef struct CLIENT_REQUEST {
    CLIENT* Client;
    uint32_t Seq;
    QUIC_BUFFER Buffer;
    char Data[2 * MAX_NAME + 2]; // "<file> <tag>"
} CLIENT_REQUEST;

static void
ClientSignal(CLIENT* Client, int* Flag)
{
    pthread_mutex_lock(&Client->Lock);
    *Flag = 1;
    pthread_cond_broadcast(&Client->Cond);
    pthread_mutex_unlock(&Client->Lock);
}

//
// Waits for *Flag, or until the connection is gone.
//
static void
ClientWait(CLIENT* Client, const int* Flag)
{
    pthread_mutex_lock(&Client->Lock);
    while (!*Flag && !Client->Finished) {
        pthread_cond_wait(&Client->Cond, &Client->Lock);
    }
    pthread_mutex_unlock(&Client->Lock);
}

static void
ClientRequestFinished(CLIENT_REQUEST* Req, int Ok)
{
    CLIENT* Client = Req->Client;
    pthread_mutex_lock(&Client->Lock);
    if (Req->Seq == Client->Seq && !Client->StreamDone) {
        Client->StreamOk = Ok;
        Client->StreamDone = 1;
        pthread_cond_broadcast(&Client->Cond);
    }
    pthread_mutex_unlock(&Client->Lock);
}

static QUIC_STATUS QUIC_API
ClientStreamCallback(HQUIC Stream, void* Context, QUIC_STREAM_EVENT* Event)
{
    CLIENT_REQUEST* Req = (CLIENT_REQUEST*)Context;
    CLIENT* Client = Req->Client;
    switch (Event->Type) {
    case QUIC_STREAM_EVENT_RECEIVE: {
        double Now = NowMs();
        if (Client->FirstByteMs == 0 && Event->RECEIVE.TotalBufferLength > 0) {
            Client->FirstByteMs = Now;
        }
        for (uint32_t i = 0; i < Event->RECEIVE.BufferCount; ++i) {
            const QUIC_BUFFER* B = &Event->RECEIVE.Buffers[i];
            if (Client->OutFd >= 0 &&
                write(Client->OutFd, B->Buffer, B->Length) != (ssize_t)B->Length) {
                fprintf(stderr, "write failed: %s\n", strerror(errno));
            }
        }
        Client->Bytes += Event->RECEIVE.TotalBufferLength;
        Client->StreamBytes += Event->RECEIVE.TotalBufferLength;
        if (Client->Bins != NULL && Now >= Client->RequestMs) {
            size_t Bin = (size_t)((Now - Client->RequestMs) / TPUT_BIN_MS);
            if (Bin < TPUT_MAX_BINS) {
                Client->Bins[Bin] += Event->RECEIVE.TotalBufferLength;
            }
        }
        break;
    }
    case QUIC_STREAM_EVENT_PEER_SEND_SHUTDOWN:
        Client->DoneMs = NowMs();
        ClientRequestFinished(Req, 1);
        break;
    case QUIC_STREAM_EVENT_PEER_SEND_ABORTED:
        fprintf(stderr, "Server aborted the download (error %llu)\n",
                (unsigned long long)Event->PEER_SEND_ABORTED.ErrorCode);
        ClientRequestFinished(Req, 0);
        break;
    case QUIC_STREAM_EVENT_SHUTDOWN_COMPLETE:
        ClientRequestFinished(Req, 0); // No-op if the request already finished.
        MsQuic->StreamClose(Stream);
        free(Req);
        break;
    default:
        break;
    }
    return QUIC_STATUS_SUCCESS;
}

static QUIC_STATUS QUIC_API
ClientConnectionCallback(HQUIC Connection, void* Context, QUIC_CONNECTION_EVENT* Event)
{
    (void)Connection;
    CLIENT* Client = (CLIENT*)Context;
    switch (Event->Type) {
    case QUIC_CONNECTION_EVENT_CONNECTED:
        Client->ConnectedMs = NowMs();
        ClientSignal(Client, &Client->Connected);
        break;
    case QUIC_CONNECTION_EVENT_SHUTDOWN_INITIATED_BY_TRANSPORT:
        fprintf(stderr, "Connection shut down by transport, 0x%x\n",
                Event->SHUTDOWN_INITIATED_BY_TRANSPORT.Status);
        break;
    case QUIC_CONNECTION_EVENT_SHUTDOWN_COMPLETE:
        ClientSignal(Client, &Client->Finished);
        break;
    default:
        break;
    }
    return QUIC_STATUS_SUCCESS;
}

//
// Requests one file on a new stream and waits until it has fully arrived.
// Returns 1 on success.
//
static int
ClientRequest(CLIENT* Client, HQUIC Connection, const char* File)
{
    CLIENT_REQUEST* Req = calloc(1, sizeof(*Req));
    if (Req == NULL) {
        return 0;
    }
    pthread_mutex_lock(&Client->Lock);
    Req->Seq = ++Client->Seq;
    Client->StreamDone = 0;
    Client->StreamOk = 0;
    pthread_mutex_unlock(&Client->Lock);
    Req->Client = Client;
    snprintf(Req->Data, sizeof(Req->Data), "%s %s", File, Client->Tag);
    Req->Buffer.Buffer = (uint8_t*)Req->Data;
    Req->Buffer.Length = (uint32_t)strlen(Req->Data);
    Client->StreamBytes = 0;
    Client->FirstByteMs = 0;

    HQUIC Stream;
    if (QUIC_FAILED(MsQuic->StreamOpen(
            Connection, QUIC_STREAM_OPEN_FLAG_NONE, ClientStreamCallback, Req, &Stream))) {
        free(Req);
        return 0;
    }
    if (QUIC_FAILED(MsQuic->StreamStart(Stream, QUIC_STREAM_START_FLAG_NONE))) {
        MsQuic->StreamClose(Stream);
        free(Req);
        return 0;
    }
    // From here on the stream callback owns Req (freed at SHUTDOWN_COMPLETE).
    Client->RequestMs = NowMs();
    if (QUIC_FAILED(MsQuic->StreamSend(Stream, &Req->Buffer, 1, QUIC_SEND_FLAG_FIN, NULL))) {
        MsQuic->StreamShutdown(Stream, QUIC_STREAM_SHUTDOWN_FLAG_ABORT, 0);
        return 0;
    }
    ClientWait(Client, &Client->StreamDone);
    pthread_mutex_lock(&Client->Lock);
    int Ok = Client->StreamDone && Client->StreamOk;
    pthread_mutex_unlock(&Client->Lock);
    return Ok;
}

static int
CompareDouble(const void* A, const void* B)
{
    double X = *(const double*)A, Y = *(const double*)B;
    return X < Y ? -1 : X > Y;
}

//
// Percentile (0-100) of N sorted values, linear interpolation.
//
static double
Percentile(const double* Sorted, size_t N, double Pct)
{
    double Pos = (N - 1) * Pct / 100.0;
    size_t Lo = (size_t)Pos;
    size_t Hi = Lo + 1 < N ? Lo + 1 : Lo;
    return Sorted[Lo] + (Sorted[Hi] - Sorted[Lo]) * (Pos - Lo);
}

//
// File mode: one download; prints RESULT and TPUT (throughput per 100 ms).
//
static int
ClientDownload(CLIENT* Client, HQUIC Connection, const char* File)
{
    Client->Bins = calloc(TPUT_MAX_BINS, sizeof(uint64_t));
    int Ok = Client->Bins != NULL && ClientRequest(Client, Connection, File);
    double CpuMs = ProcessCpuMs() - Client->StartCpuMs;
    double SysCpuPct = SystemCpuPct(Client->StartSys);
    if (!Ok) {
        free(Client->Bins);
        return 0;
    }

    double TransferMs = Client->DoneMs - Client->RequestMs;
    // Throughput in each complete 100 ms bin of the transfer (the last, partial bin is dropped).
    size_t NumBins = (size_t)(TransferMs / TPUT_BIN_MS);
    if (NumBins > TPUT_MAX_BINS) {
        NumBins = TPUT_MAX_BINS;
    }
    double* Mbps = malloc((NumBins + 1) * sizeof(double));
    double* Sorted = malloc((NumBins + 1) * sizeof(double));
    for (size_t i = 0; Mbps != NULL && Sorted != NULL && i < NumBins; ++i) {
        Mbps[i] = Sorted[i] = Client->Bins[i] * 8.0 / (TPUT_BIN_MS * 1e3);
    }
    printf("RESULT tag=%s file=%s bytes=%llu policy=%s download_ms=%.2f handshake_ms=%.2f "
           "ttfb_ms=%.2f transfer_ms=%.2f goodput_mbps=%.1f cpu_ms=%.1f sys_cpu_pct=%.1f "
           "tput_bins=%zu ",
           Client->Tag, File, (unsigned long long)Client->Bytes,
           getenv("MCN_ACK_POLICY"), Client->DoneMs - Client->StartMs,
           Client->ConnectedMs - Client->StartMs,
           Client->FirstByteMs ? Client->FirstByteMs - Client->RequestMs : 0, TransferMs,
           Client->Bytes * 8 / (TransferMs * 1e3), CpuMs, SysCpuPct, NumBins);
    if (Mbps != NULL && Sorted != NULL && NumBins >= 3) {
        qsort(Sorted, NumBins, sizeof(double), CompareDouble);
        printf("tput_p10_mbps=%.1f tput_p50_mbps=%.1f tput_p90_mbps=%.1f ",
               Percentile(Sorted, NumBins, 10), Percentile(Sorted, NumBins, 50),
               Percentile(Sorted, NumBins, 90));
    }
    PrintStats("client", Connection);
    printf("\n");
    if (Mbps != NULL) {
        printf("TPUT tag=%s bin_ms=%d mbps=", Client->Tag, TPUT_BIN_MS);
        for (size_t i = 0; i < NumBins; ++i) {
            printf("%s%.0f", i ? "," : "", Mbps[i]);
        }
        printf("\n");
    }
    fflush(stdout);
    free(Mbps);
    free(Sorted);
    free(Client->Bins);
    Client->Bins = NULL;
    return 1;
}

//
// Video mode: plays a VoD stream of fixed-duration segments over one connection,
// one stream per segment, with a throughput-based ABR rule and a real-time
// playback buffer. Segment files are "video_<rate>M.bin" (see make-testdata.sh).
//
//   ABR: rate = highest ladder rate <= 0.9 x harmonic mean throughput of the last
//        3 segments (the first segment uses the lowest rate).
//   Playback starts when the first segment has arrived; the client does not
//   request the next segment while the buffer holds more than bufmax - segdur.
//   QoE (Yin et al., SIGCOMM'15, linear form), per segment:
//        (sum R_k - sum |R_k - R_k-1| - R_max * (rebuffer_s + startup_s)) / N
//
// Prints one SEG line per segment and one VIDEO line.
//
static int
ClientVideo(CLIENT* Client, HQUIC Connection, int argc, char** argv)
{
    double Ladder[MAX_LADDER];
    int Rungs = 0;
    const char* LadderArg = GetArg(argc, argv, "ladder");
    char LadderCopy[256];
    snprintf(LadderCopy, sizeof(LadderCopy), "%s", LadderArg ? LadderArg : "25,50,100,200,300,400");
    for (char* Save = NULL, *T = strtok_r(LadderCopy, ",", &Save);
         T != NULL && Rungs < MAX_LADDER; T = strtok_r(NULL, ",", &Save)) {
        Ladder[Rungs++] = atof(T);
    }
    const char* Arg;
    int Segments = (Arg = GetArg(argc, argv, "segments")) ? atoi(Arg) : 20;
    double SegS = (Arg = GetArg(argc, argv, "segdur")) ? atof(Arg) : 2.0;
    double BufMaxS = (Arg = GetArg(argc, argv, "bufmax")) ? atof(Arg) : 10.0;
    if (Rungs == 0 || Segments <= 0 || SegS <= 0 || BufMaxS < SegS) {
        fprintf(stderr, "bad -ladder/-segments/-segdur/-bufmax\n");
        return 0;
    }

    double TputHist[3] = {0};
    double* SegTput = calloc((size_t)Segments, sizeof(double));
    if (SegTput == NULL) {
        return 0;
    }
    double BufS = 0, LastMs = 0, StartupMs = 0, RebufMs = 0, SumRate = 0, SumSwitch = 0;
    int Done = 0, RebufEvents = 0, Switches = 0, Prev = -1;
    for (int k = 0; k < Segments; ++k) {
        if (k > 0) {
            // Drain the buffer up to now; wait while it is full.
            double Now = NowMs();
            BufS -= (Now - LastMs) / 1e3; // Cannot run dry here: a segment just arrived.
            LastMs = Now;
            if (BufS > BufMaxS - SegS) {
                usleep((useconds_t)((BufS - (BufMaxS - SegS)) * 1e6));
                Now = NowMs();
                BufS -= (Now - LastMs) / 1e3;
                LastMs = Now;
            }
        }
        int Q = 0;
        if (k > 0) {
            int H = k < 3 ? k : 3;
            double Inv = 0;
            for (int i = 0; i < H; ++i) {
                Inv += 1.0 / TputHist[i];
            }
            double Est = H / Inv;
            for (int i = Rungs - 1; i >= 0; --i) {
                if (Ladder[i] <= 0.9 * Est) {
                    Q = i;
                    break;
                }
            }
        }
        char File[64];
        snprintf(File, sizeof(File), "video_%gM.bin", Ladder[Q]);
        double ReqMs = NowMs();
        if (!ClientRequest(Client, Connection, File)) {
            break;
        }
        double SegMs = Client->DoneMs - ReqMs;
        double Tput = Client->StreamBytes * 8 / (SegMs * 1e3);
        TputHist[k % 3] = Tput;
        SegTput[k] = Tput;
        double StallMs = 0;
        if (k == 0) {
            StartupMs = Client->DoneMs - Client->StartMs;
            BufS = SegS;
        } else {
            double Drain = (Client->DoneMs - LastMs) / 1e3;
            if (Drain > BufS) {
                StallMs = (Drain - BufS) * 1e3;
                RebufMs += StallMs;
                RebufEvents++;
                BufS = 0;
            } else {
                BufS -= Drain;
            }
            BufS += SegS;
        }
        LastMs = Client->DoneMs;
        SumRate += Ladder[Q];
        if (Prev >= 0 && Q != Prev) {
            Switches++;
            SumSwitch += Ladder[Q] > Ladder[Prev] ? Ladder[Q] - Ladder[Prev] : Ladder[Prev] - Ladder[Q];
        }
        Prev = Q;
        Done++;
        printf("SEG tag=%s k=%d rate_mbps=%g bytes=%llu ms=%.1f tput_mbps=%.1f buffer_s=%.2f "
               "stall_ms=%.1f t_ms=%.1f\n",
               Client->Tag, k, Ladder[Q], (unsigned long long)Client->StreamBytes, SegMs, Tput,
               BufS, StallMs, Client->DoneMs - Client->StartMs);
    }
    double CpuMs = ProcessCpuMs() - Client->StartCpuMs;
    double SysCpuPct = SystemCpuPct(Client->StartSys);
    if (Done < Segments) {
        free(SegTput);
        return 0;
    }
    double RMax = Ladder[Rungs - 1];
    double Qoe = (SumRate - SumSwitch - RMax * (RebufMs + StartupMs) / 1e3) / Segments;
    qsort(SegTput, (size_t)Segments, sizeof(double), CompareDouble);
    printf("VIDEO tag=%s file=video bytes=%llu policy=%s segments=%d seg_s=%g bufmax_s=%g "
           "startup_ms=%.1f avg_bitrate_mbps=%.1f switches=%d switch_mbps=%.1f "
           "rebuffer_events=%d rebuffer_ms=%.1f qoe=%.2f session_ms=%.1f "
           "seg_tput_p10_mbps=%.1f seg_tput_p50_mbps=%.1f cpu_ms=%.1f sys_cpu_pct=%.1f ",
           Client->Tag, (unsigned long long)Client->Bytes, getenv("MCN_ACK_POLICY"), Segments,
           SegS, BufMaxS, StartupMs, SumRate / Segments, Switches,
           Switches ? SumSwitch / Switches : 0, RebufEvents, RebufMs, Qoe,
           LastMs - Client->StartMs, Percentile(SegTput, (size_t)Segments, 10),
           Percentile(SegTput, (size_t)Segments, 50), CpuMs, SysCpuPct);
    PrintStats("client", Connection);
    printf("\n");
    fflush(stdout);
    free(SegTput);
    return 1;
}

static int
RunClient(int argc, char** argv)
{
    const char* Target = GetArg(argc, argv, "target");
    const char* PortArg = GetArg(argc, argv, "port");
    const char* Out = GetArg(argc, argv, "out");
    const char* File = GetArg(argc, argv, "file");
    const char* Video = GetArg(argc, argv, "video");
    int VideoMode = Video != NULL && atoi(Video) != 0;
    CLIENT Client = {0};
    Client.Tag = GetArg(argc, argv, "tag");
    Client.OutFd = -1;
    if (Target == NULL || (File == NULL && !VideoMode)) {
        fprintf(stderr, "client needs -target and -file (or -video:1)\n");
        return 1;
    }
    if (Client.Tag == NULL) {
        Client.Tag = "-";
    }
    if ((File != NULL && strlen(File) > MAX_NAME) || strlen(Client.Tag) > MAX_NAME ||
        strchr(Client.Tag, ' ') != NULL) {
        fprintf(stderr, "-file or -tag too long, or -tag contains a space\n");
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
    Client.StartSys = SystemCpu();
    if (QUIC_FAILED(Status = MsQuic->ConnectionStart(
            Connection, Configuration, QUIC_ADDRESS_FAMILY_UNSPEC, Target,
            PortArg ? (uint16_t)atoi(PortArg) : DEFAULT_PORT))) {
        fprintf(stderr, "ConnectionStart failed, 0x%x\n", Status);
        MsQuic->ConnectionClose(Connection);
        return 1;
    }

    ClientWait(&Client, &Client.Connected);
    int Ok = 0;
    if (Client.Connected) {
        Ok = VideoMode ? ClientVideo(&Client, Connection, argc, argv)
                       : ClientDownload(&Client, Connection, File);
    }

    MsQuic->ConnectionShutdown(Connection, QUIC_CONNECTION_SHUTDOWN_FLAG_NONE, 0);
    ClientWait(&Client, &Client.Finished);
    MsQuic->ConnectionClose(Connection);
    if (Client.OutFd >= 0) {
        close(Client.OutFd);
    }
    return Ok ? 0 : 1;
}

int
main(int argc, char** argv)
{
    if (argc < 2 || (strcmp(argv[1], "server") != 0 && strcmp(argv[1], "client") != 0)) {
        fprintf(stderr,
            "usage: %s server -dir:<path> -cert:<file> -key:<file> [-port:N] [options]\n"
            "       %s client -target:<ip> -file:<name> [-port:N] [-out:<path>] [-tag:<id>] [options]\n"
            "       %s client -target:<ip> -video:1 [-ladder:Mbps,...] [-segments:N] [-segdur:S]\n"
            "                [-bufmax:S] [-port:N] [-tag:<id>] [options]\n"
            "options: -policy:default|fixed|litequic|adaptive -threshold:N -acknet:0|1 -ackmax:N\n"
            "         -ackphase:1 -ackreorder:N -ackdelay:1 -cc:cubic|bbr -acklog:1\n",
            argv[0], argv[0], argv[0]);
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
