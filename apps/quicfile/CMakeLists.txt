cmake_minimum_required(VERSION 3.16)
project(quicfile C)

# Built against the msquic tree in this repo (see scripts/setup.sh).
set(MSQUIC_SOURCE_DIR "${CMAKE_CURRENT_LIST_DIR}/../../msquic" CACHE PATH "msquic source tree")
set(MSQUIC_LIB_DIR "" CACHE PATH "Directory containing libmsquic.so")

find_library(MSQUIC_LIB msquic PATHS "${MSQUIC_LIB_DIR}" NO_DEFAULT_PATH REQUIRED)
find_package(Threads REQUIRED)

add_executable(quicfile quicfile.c)
target_include_directories(quicfile PRIVATE "${MSQUIC_SOURCE_DIR}/src/inc")
target_compile_options(quicfile PRIVATE -Wall -Wextra -O2)
target_link_libraries(quicfile PRIVATE "${MSQUIC_LIB}" Threads::Threads)
set_target_properties(quicfile PROPERTIES BUILD_RPATH "${MSQUIC_LIB_DIR}")
