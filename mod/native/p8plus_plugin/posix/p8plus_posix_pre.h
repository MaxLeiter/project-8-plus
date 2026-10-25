/*
 * P8Plus native, Linux build: included before every source (-include, CMakeLists.txt if(UNIX)).
 * MSVC's __declspec(thread) (p8plus_widescreen.c) is C11 _Thread_local here.
 */
#ifndef P8PLUS_POSIX_PRE_H
#define P8PLUS_POSIX_PRE_H
#define __declspec(x) P8PLUS_DECLSPEC_##x
#define P8PLUS_DECLSPEC_thread _Thread_local
#endif
