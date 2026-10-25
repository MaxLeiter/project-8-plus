/*
 * p8audio - soundtrack converter of the Project 8 Plus modding kit (tools/modkit/p8audio.py, ops_music.py).
 *
 *   p8audio prepare --out OUT.mp3 [--lufs -21] [--kbps 192] [--no-trim] IN [IN...]
 *       decode every IN (stems are summed), 44.1 kHz stereo, trim silence at both ends, gain to the target
 *       integrated loudness (ITU-R BS.1770-4 / EBU R128 gating) with a -1 dBFS look-ahead peak limiter, encode a
 *       raw CBR MP3 stream (no ID3) with the Windows Media Foundation MP3 encoder.
 *       stdout: one JSON object {samples, rate, seconds, lufs_in, peak_in, gain_db, bytes}; errors on stderr, exit 1.
 *   p8audio probe IN        -> JSON {rate, channels, seconds} (decode check)
 *
 * Decoding: Media Foundation (MP3, AAC/M4A, FLAC, WAV, WMA, ALAC, ...); OGG Vorbis through stb_vorbis when built
 * with -DP8_STB_VORBIS (third_party/stb_vorbis.c), else Media Foundation too (works when the Web Media Extensions
 * are installed). Windows 8 or newer (MP3 encoder MFT).
 *
 * Build: zig cc -target x86_64-windows-gnu -O2 p8audio.c -o p8audio.exe -lmfplat -lmfreadwrite -lmfuuid -lole32
 *        (build.sh). No other dependency; the player's files never leave their PC.
 */
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <mferror.h>
#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef P8_STB_VORBIS
#define STB_VORBIS_NO_PUSHDATA_API
#include "third_party/stb_vorbis.c"
#endif

#define OUT_RATE 44100
#define CEILING 0.841f /* -1.5 dBFS (MP3 overshoot stays under 0 dBFS) */

typedef struct {
    float* d; /* interleaved */
    size_t n; /* frames */
    int ch;
    int rate;
} Pcm;

static void die(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    exit(1);
}

static void* xrealloc(void* p, size_t n) {
    void* q = realloc(p, n ? n : 1);
    if (!q) die("out of memory (%zu bytes)", n);
    return q;
}

static char* narrow(const wchar_t* w) {
    static char buf[4 * MAX_PATH];
    WideCharToMultiByte(CP_UTF8, 0, w, -1, buf, sizeof buf, NULL, NULL);
    return buf;
}

static const wchar_t* base_name(const wchar_t* p) {
    const wchar_t* b = p;
    for (const wchar_t* s = p; *s; s++)
        if (*s == L'\\' || *s == L'/') b = s + 1;
    return b;
}

/* ------------------------------------------------------------------------------------------------ decode */
static int decode_mf(const wchar_t* path, Pcm* out, char* err, size_t errn) {
    IMFSourceReader* r = NULL;
    IMFMediaType *want = NULL, *got = NULL;
    HRESULT hr = MFCreateSourceReaderFromURL(path, NULL, &r);
    if (FAILED(hr)) {
        snprintf(err, errn, "Media Foundation cannot open it (0x%08lx)", (unsigned long)hr);
        return 0;
    }
    IMFSourceReader_SetStreamSelection(r, (DWORD)MF_SOURCE_READER_ALL_STREAMS, FALSE);
    IMFSourceReader_SetStreamSelection(r, (DWORD)MF_SOURCE_READER_FIRST_AUDIO_STREAM, TRUE);
    MFCreateMediaType(&want);
    IMFMediaType_SetGUID(want, &MF_MT_MAJOR_TYPE, &MFMediaType_Audio);
    IMFMediaType_SetGUID(want, &MF_MT_SUBTYPE, &MFAudioFormat_Float);
    hr = IMFSourceReader_SetCurrentMediaType(r, (DWORD)MF_SOURCE_READER_FIRST_AUDIO_STREAM, NULL, want);
    IMFMediaType_Release(want);
    if (FAILED(hr)) {
        snprintf(err, errn, "no decoder for this audio format (0x%08lx)", (unsigned long)hr);
        IMFSourceReader_Release(r);
        return 0;
    }
    IMFSourceReader_GetCurrentMediaType(r, (DWORD)MF_SOURCE_READER_FIRST_AUDIO_STREAM, &got);
    UINT32 ch = 0, rate = 0, bits = 0;
    IMFMediaType_GetUINT32(got, &MF_MT_AUDIO_NUM_CHANNELS, &ch);
    IMFMediaType_GetUINT32(got, &MF_MT_AUDIO_SAMPLES_PER_SECOND, &rate);
    IMFMediaType_GetUINT32(got, &MF_MT_AUDIO_BITS_PER_SAMPLE, &bits);
    IMFMediaType_Release(got);
    if (!ch || !rate || bits != 32) {
        snprintf(err, errn, "unexpected decoder output (%u ch, %u Hz, %u bit)", ch, rate, bits);
        IMFSourceReader_Release(r);
        return 0;
    }
    size_t cap = 0, n = 0;
    float* d = NULL;
    for (;;) {
        DWORD flags = 0;
        IMFSample* s = NULL;
        hr = IMFSourceReader_ReadSample(r, (DWORD)MF_SOURCE_READER_FIRST_AUDIO_STREAM, 0, NULL, &flags, NULL, &s);
        if (FAILED(hr)) {
            snprintf(err, errn, "decode error (0x%08lx)", (unsigned long)hr);
            free(d);
            IMFSourceReader_Release(r);
            return 0;
        }
        if (s) {
            IMFMediaBuffer* b = NULL;
            if (SUCCEEDED(IMFSample_ConvertToContiguousBuffer(s, &b))) {
                BYTE* p;
                DWORD len;
                if (SUCCEEDED(IMFMediaBuffer_Lock(b, &p, NULL, &len))) {
                    size_t fr = len / (4 * ch);
                    if ((n + fr) * ch > cap) {
                        cap = ((n + fr) * ch) * 3 / 2 + 65536;
                        d = xrealloc(d, cap * sizeof(float));
                    }
                    memcpy(d + n * ch, p, fr * ch * 4);
                    n += fr;
                    IMFMediaBuffer_Unlock(b);
                }
                IMFMediaBuffer_Release(b);
            }
            IMFSample_Release(s);
        }
        if (flags & MF_SOURCE_READERF_ENDOFSTREAM) break;
    }
    IMFSourceReader_Release(r);
    out->d = d;
    out->n = n;
    out->ch = (int)ch;
    out->rate = (int)rate;
    return 1;
}

#ifdef P8_STB_VORBIS
static int decode_ogg(const wchar_t* path, Pcm* out, char* err, size_t errn) {
    FILE* f = _wfopen(path, L"rb");
    if (!f) {
        snprintf(err, errn, "cannot open");
        return 0;
    }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char* buf = xrealloc(NULL, (size_t)sz);
    size_t got = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    int ch = 0, rate = 0;
    short* pcm = NULL;
    int frames = stb_vorbis_decode_memory(buf, (int)got, &ch, &rate, &pcm);
    free(buf);
    if (frames <= 0 || !pcm) {
        snprintf(err, errn, "not a decodable Ogg Vorbis file");
        return 0;
    }
    out->d = xrealloc(NULL, (size_t)frames * ch * sizeof(float));
    for (size_t i = 0; i < (size_t)frames * ch; i++) out->d[i] = pcm[i] / 32768.0f;
    free(pcm);
    out->n = (size_t)frames;
    out->ch = ch;
    out->rate = rate;
    return 1;
}
#endif

static int is_ogg(const wchar_t* path) {
    FILE* f = _wfopen(path, L"rb");
    unsigned char m[4] = {0};
    if (!f) return 0;
    size_t k = fread(m, 1, 4, f);
    fclose(f);
    return k == 4 && memcmp(m, "OggS", 4) == 0;
}

static void decode(const wchar_t* path, Pcm* out) {
    char err[256] = "";
    int ok = 0;
#ifdef P8_STB_VORBIS
    if (is_ogg(path)) ok = decode_ogg(path, out, err, sizeof err);
#endif
    if (!ok) {
        char e2[256];
        if (decode_mf(path, out, e2, sizeof e2)) ok = 1;
        else if (!err[0]) snprintf(err, sizeof err, "%s%s", e2,
                                   is_ogg(path) ? " (Ogg: this build has no Vorbis decoder and Windows has none)" : "");
    }
    if (!ok) die("%s: %s", narrow(base_name(path)), err);
    if (out->n == 0) die("%s: no audio", narrow(base_name(path)));
}

/* to stereo */
static void to_stereo(Pcm* p) {
    if (p->ch == 2) return;
    float* d = xrealloc(NULL, p->n * 2 * sizeof(float));
    for (size_t i = 0; i < p->n; i++) {
        const float* s = p->d + i * p->ch;
        float l, r;
        if (p->ch == 1) l = r = s[0];
        else {
            l = s[0];
            r = s[1];
            if (p->ch >= 3) { l += 0.7071f * s[2]; r += 0.7071f * s[2]; }          /* centre */
            if (p->ch >= 6) { l += 0.7071f * s[4]; r += 0.7071f * s[5]; }          /* 5.1 surrounds */
        }
        d[2 * i] = l;
        d[2 * i + 1] = r;
    }
    free(p->d);
    p->d = d;
    p->ch = 2;
}

/* windowed-sinc resampler (Blackman, 32 zero crossings per side at the lower rate), polyphase table with 1/1024
   sample phase steps, taps normalised per phase */
static void resample(Pcm* p, int to) {
    if (p->rate == to) return;
    const double ratio = (double)p->rate / to;
    const double fc = ratio > 1.0 ? 1.0 / ratio : 1.0;     /* cutoff relative to the input Nyquist */
    const int half = (int)ceil(32.0 / fc), taps = 2 * half, PH = 1024;
    float* tab = xrealloc(NULL, (size_t)(PH + 1) * taps * sizeof(float));
    for (int ph = 0; ph <= PH; ph++) {
        double frac = (double)ph / PH, ws = 0;
        float* h = tab + (size_t)ph * taps;
        for (int j = 0; j < taps; j++) {
            double d = frac + (half - 1 - j);  /* t - k for k = c - half + 1 + j */
            double x = d * fc, u = d / half, v = 0;
            if (u > -1.0 && u < 1.0) {
                double sinc = fabs(x) < 1e-9 ? 1.0 : sin(M_PI * x) / (M_PI * x);
                v = sinc * (0.42 + 0.5 * cos(M_PI * u) + 0.08 * cos(2 * M_PI * u));
            }
            h[j] = (float)v;
            ws += v;
        }
        for (int j = 0; j < taps; j++) h[j] = (float)(h[j] / ws);
    }
    size_t on = (size_t)((double)p->n / ratio);
    float* d = xrealloc(NULL, on * 2 * sizeof(float));
    for (size_t i = 0; i < on; i++) {
        double t = i * ratio;
        long c = (long)floor(t);
        const float* h = tab + (size_t)lrint((t - c) * PH) * taps;
        long k0 = c - half + 1;
        float l = 0, r = 0;
        if (k0 >= 0 && (size_t)(k0 + taps) <= p->n) {
            const float* s = p->d + 2 * k0;
            for (int j = 0; j < taps; j++) { l += h[j] * s[2 * j]; r += h[j] * s[2 * j + 1]; }
        } else {
            for (int j = 0; j < taps; j++) {
                long k = k0 + j;
                if (k < 0 || (size_t)k >= p->n) continue;
                l += h[j] * p->d[2 * k];
                r += h[j] * p->d[2 * k + 1];
            }
        }
        d[2 * i] = l;
        d[2 * i + 1] = r;
    }
    free(tab);
    free(p->d);
    p->d = d;
    p->n = on;
    p->rate = to;
}

/* ------------------------------------------------------------------------------------------------ loudness */
typedef struct { double b0, b1, b2, a1, a2, z1, z2; } Biquad;

static Biquad bq(double b0, double b1, double b2, double a0, double a1, double a2) {
    Biquad q = {b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0, 0, 0};
    return q;
}

static inline double bq_run(Biquad* q, double x) {
    double y = q->b0 * x + q->z1;
    q->z1 = q->b1 * x - q->a1 * y + q->z2;
    q->z2 = q->b2 * x - q->a2 * y;
    return y;
}

/* ITU-R BS.1770-4 integrated loudness (LUFS) of stereo PCM, -inf (returned as -200) for silence */
static double integrated_lufs(const Pcm* p) {
    const double fs = p->rate;
    double A = pow(10.0, 4.0 / 40.0), w0 = 2 * M_PI * 1500.0 / fs, al = sin(w0) / (2 * (1 / sqrt(2.0)));
    double cw = cos(w0), sA = sqrt(A);
    Biquad pre[2], hp[2];
    for (int c = 0; c < 2; c++) {
        pre[c] = bq(A * ((A + 1) + (A - 1) * cw + 2 * sA * al), -2 * A * ((A - 1) + (A + 1) * cw),
                    A * ((A + 1) + (A - 1) * cw - 2 * sA * al), (A + 1) - (A - 1) * cw + 2 * sA * al,
                    2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - 2 * sA * al);
    }
    double w1 = 2 * M_PI * 38.0 / fs, a2 = sin(w1) / (2 * 0.5), c1 = cos(w1);
    for (int c = 0; c < 2; c++) hp[c] = bq((1 + c1) / 2, -(1 + c1), (1 + c1) / 2, 1 + a2, -2 * c1, 1 - a2);
    size_t step = (size_t)(fs * 0.1);          /* 100 ms; a block = 4 steps (400 ms, 75 % overlap) */
    size_t nsteps = p->n / step;
    if (nsteps < 4) return -200;
    double* e = xrealloc(NULL, nsteps * sizeof(double));
    for (size_t s = 0; s < nsteps; s++) {
        double acc = 0;
        for (size_t i = s * step; i < (s + 1) * step; i++)
            for (int c = 0; c < 2; c++) {
                double y = bq_run(&hp[c], bq_run(&pre[c], p->d[2 * i + c]));
                acc += y * y;
            }
        e[s] = acc / step;
    }
    size_t nb = nsteps - 3;
    double* z = xrealloc(NULL, nb * sizeof(double));
    for (size_t b = 0; b < nb; b++) z[b] = (e[b] + e[b + 1] + e[b + 2] + e[b + 3]) / 4;
    double sum = 0;
    size_t cnt = 0;
    const double abs_gate = pow(10.0, (-70.0 + 0.691) / 10.0);
    for (size_t b = 0; b < nb; b++)
        if (z[b] > abs_gate) { sum += z[b]; cnt++; }
    if (!cnt) { free(e); free(z); return -200; }
    double rel_gate = (sum / cnt) * pow(10.0, -10.0 / 10.0);
    sum = 0;
    cnt = 0;
    for (size_t b = 0; b < nb; b++)
        if (z[b] > abs_gate && z[b] > rel_gate) { sum += z[b]; cnt++; }
    free(e);
    free(z);
    return cnt ? -0.691 + 10 * log10(sum / cnt) : -200;
}

/* ------------------------------------------------------------------------------------------------ dsp */
static void trim(Pcm* p) {
    const float th = 0.00178f; /* -55 dBFS */
    size_t a = 0, b = p->n;
    while (a < p->n && fabsf(p->d[2 * a]) < th && fabsf(p->d[2 * a + 1]) < th) a++;
    while (b > a && fabsf(p->d[2 * (b - 1)]) < th && fabsf(p->d[2 * (b - 1) + 1]) < th) b--;
    size_t pad = (size_t)(0.05 * p->rate);
    a = a > pad ? a - pad : 0;
    b = b + pad < p->n ? b + pad : p->n;
    memmove(p->d, p->d + 2 * a, (b - a) * 2 * sizeof(float));
    p->n = b - a;
    size_t f = (size_t)(0.005 * p->rate); /* 5 ms fades */
    for (size_t i = 0; i < f && i < p->n; i++) {
        float g = (float)i / f;
        p->d[2 * i] *= g;
        p->d[2 * i + 1] *= g;
        p->d[2 * (p->n - 1 - i)] *= g;
        p->d[2 * (p->n - 1 - i) + 1] *= g;
    }
}

/* gain + look-ahead peak limiter at CEILING; returns the input sample peak in dBFS */
static double gain_limit(Pcm* p, double gain_db, int* limited) {
    const float g0 = (float)pow(10.0, gain_db / 20.0);
    const size_t n = p->n, W = (size_t)(0.005 * p->rate);
    float* req = xrealloc(NULL, n * sizeof(float));
    double pk = 0;
    int any = 0;
    for (size_t i = 0; i < n; i++) {
        float a = fmaxf(fabsf(p->d[2 * i]), fabsf(p->d[2 * i + 1]));
        if (a > pk) pk = a;
        float x = a * g0;
        req[i] = x > CEILING ? CEILING / x : 1.0f;
        if (x > CEILING) any = 1;
    }
    *limited = any;
    if (any) {
        /* m[i] = min(req[i..i+W]) (deque), then a box average over the W previous m values, then release */
        float* m = xrealloc(NULL, n * sizeof(float));
        size_t* dq = xrealloc(NULL, (n + 1) * sizeof(size_t));
        size_t h = 0, t = 0;
        for (size_t j = n; j-- > 0;) {        /* backwards: window [j, j+W] */
            while (t > h && req[dq[t - 1]] >= req[j]) t--;
            dq[t++] = j;
            while (dq[h] > j + W) h++;
            m[j] = req[dq[h]];
        }
        free(dq);
        double acc = 0, g = 1.0, rel = 1.0 - exp(-1.0 / (0.06 * p->rate));
        for (size_t i = 0; i < n; i++) {
            acc += m[i];
            if (i >= W) acc -= m[i - W];
            double av = acc / (double)(i + 1 < W ? i + 1 : W);
            if (av > m[i]) av = m[i]; /* never above the window minimum at i (safety at the ramp start) */
            if (av < g) g = av;
            else g += (av - g) * rel;
            req[i] = (float)g;
        }
        free(m);
    }
    for (size_t i = 0; i < n; i++) {
        float g = g0 * (any ? req[i] : 1.0f);
        p->d[2 * i] *= g;
        p->d[2 * i + 1] *= g;
    }
    free(req);
    return pk > 0 ? 20 * log10(pk) : -200;
}

/* ------------------------------------------------------------------------------------------------ encode */
static void encode_mp3(const Pcm* p, const wchar_t* out, int kbps) {
    IMFSinkWriter* w = NULL;
    IMFAttributes* at = NULL;
    MFCreateAttributes(&at, 1);
    IMFAttributes_SetGUID(at, &MF_TRANSCODE_CONTAINERTYPE, &MFTranscodeContainerType_MP3);
    DeleteFileW(out);
    HRESULT hr = MFCreateSinkWriterFromURL(out, NULL, at, &w);
    IMFAttributes_Release(at);
    if (FAILED(hr)) die("cannot create %s (0x%08lx)", narrow(out), (unsigned long)hr);
    IMFMediaType *ot = NULL, *it = NULL;
    MFCreateMediaType(&ot);
    IMFMediaType_SetGUID(ot, &MF_MT_MAJOR_TYPE, &MFMediaType_Audio);
    IMFMediaType_SetGUID(ot, &MF_MT_SUBTYPE, &MFAudioFormat_MP3);
    IMFMediaType_SetUINT32(ot, &MF_MT_AUDIO_NUM_CHANNELS, 2);
    IMFMediaType_SetUINT32(ot, &MF_MT_AUDIO_SAMPLES_PER_SECOND, OUT_RATE);
    IMFMediaType_SetUINT32(ot, &MF_MT_AUDIO_AVG_BYTES_PER_SECOND, (UINT32)kbps * 125);
    DWORD si = 0;
    hr = IMFSinkWriter_AddStream(w, ot, &si);
    IMFMediaType_Release(ot);
    if (FAILED(hr)) die("the Windows MP3 encoder refused %d kbps 44.1 kHz stereo (0x%08lx)", kbps, (unsigned long)hr);
    MFCreateMediaType(&it);
    IMFMediaType_SetGUID(it, &MF_MT_MAJOR_TYPE, &MFMediaType_Audio);
    IMFMediaType_SetGUID(it, &MF_MT_SUBTYPE, &MFAudioFormat_PCM);
    IMFMediaType_SetUINT32(it, &MF_MT_AUDIO_NUM_CHANNELS, 2);
    IMFMediaType_SetUINT32(it, &MF_MT_AUDIO_SAMPLES_PER_SECOND, OUT_RATE);
    IMFMediaType_SetUINT32(it, &MF_MT_AUDIO_BITS_PER_SAMPLE, 16);
    IMFMediaType_SetUINT32(it, &MF_MT_AUDIO_BLOCK_ALIGNMENT, 4);
    IMFMediaType_SetUINT32(it, &MF_MT_AUDIO_AVG_BYTES_PER_SECOND, OUT_RATE * 4);
    IMFMediaType_SetUINT32(it, &MF_MT_ALL_SAMPLES_INDEPENDENT, TRUE);
    hr = IMFSinkWriter_SetInputMediaType(w, si, it, NULL);
    IMFMediaType_Release(it);
    if (FAILED(hr)) die("MP3 encoder input type refused (0x%08lx)", (unsigned long)hr);
    if (FAILED(hr = IMFSinkWriter_BeginWriting(w))) die("BeginWriting failed (0x%08lx)", (unsigned long)hr);
    const size_t CH = 4410;
    uint32_t rng = 22222;
    for (size_t i = 0; i < p->n; i += CH) {
        size_t fr = p->n - i < CH ? p->n - i : CH;
        IMFMediaBuffer* b = NULL;
        IMFSample* s = NULL;
        MFCreateMemoryBuffer((DWORD)(fr * 4), &b);
        BYTE* q;
        IMFMediaBuffer_Lock(b, &q, NULL, NULL);
        int16_t* o = (int16_t*)q;
        for (size_t k = 0; k < fr * 2; k++) {
            rng = rng * 1664525u + 1013904223u;
            float d1 = (rng >> 8) / 16777216.0f;
            rng = rng * 1664525u + 1013904223u;
            float d2 = (rng >> 8) / 16777216.0f;
            float v = p->d[2 * i + k] * 32767.0f + (d1 - d2); /* TPDF dither */
            long x = lrintf(v);
            o[k] = (int16_t)(x > 32767 ? 32767 : x < -32768 ? -32768 : x);
        }
        IMFMediaBuffer_Unlock(b);
        IMFMediaBuffer_SetCurrentLength(b, (DWORD)(fr * 4));
        MFCreateSample(&s);
        IMFSample_AddBuffer(s, b);
        IMFSample_SetSampleTime(s, (LONGLONG)i * 10000000LL / OUT_RATE);
        IMFSample_SetSampleDuration(s, (LONGLONG)fr * 10000000LL / OUT_RATE);
        hr = IMFSinkWriter_WriteSample(w, si, s);
        IMFSample_Release(s);
        IMFMediaBuffer_Release(b);
        if (FAILED(hr)) die("MP3 encode failed (0x%08lx)", (unsigned long)hr);
    }
    if (FAILED(hr = IMFSinkWriter_Finalize(w))) die("MP3 finalize failed (0x%08lx)", (unsigned long)hr);
    IMFSinkWriter_Release(w);
}

/* the game wants a bare MPEG frame stream: drop ID3v2 / ID3v1 tags the file sink may add */
static long strip_tags(const wchar_t* path) {
    FILE* f = _wfopen(path, L"rb");
    if (!f) die("cannot read %s", narrow(path));
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char* b = xrealloc(NULL, (size_t)sz);
    size_t got = fread(b, 1, (size_t)sz, f);
    fclose(f);
    size_t a = 0, e = got;
    while (e - a > 10 && memcmp(b + a, "ID3", 3) == 0) {
        size_t s = ((b[a + 6] & 127) << 21) | ((b[a + 7] & 127) << 14) | ((b[a + 8] & 127) << 7) | (b[a + 9] & 127);
        a += 10 + s + ((b[a + 5] & 0x10) ? 10 : 0);
    }
    while (a + 1 < e && !(b[a] == 0xFF && (b[a + 1] & 0xE0) == 0xE0)) a++;   /* to the first frame sync */
    if (e - a > 128 && memcmp(b + e - 128, "TAG", 3) == 0) e -= 128;
    if (a + 1 >= e) die("the MP3 encoder wrote no frames");
    f = _wfopen(path, L"wb");
    fwrite(b + a, 1, e - a, f);
    fclose(f);
    free(b);
    return (long)(e - a);
}

/* ------------------------------------------------------------------------------------------------ main */
int main(void) {
    int argc;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argc < 3) {
        fprintf(stderr, "usage: p8audio prepare --out OUT.mp3 [--lufs -21] [--kbps 192] [--no-trim] IN [IN...]\n"
                        "       p8audio probe IN\n");
        return 2;
    }
    if (FAILED(CoInitializeEx(NULL, COINIT_MULTITHREADED)) || FAILED(MFStartup(MF_VERSION, MFSTARTUP_LITE)))
        die("Media Foundation is not available on this Windows");
    if (wcscmp(argv[1], L"probe") == 0) {
        Pcm p = {0};
        decode(argv[2], &p);
        printf("{\"rate\": %d, \"channels\": %d, \"seconds\": %.3f}\n", p.rate, p.ch, (double)p.n / p.rate);
        return 0;
    }
    if (wcscmp(argv[1], L"prepare") != 0) die("unknown command");
    const wchar_t* out = NULL;
    double lufs = -21.0;
    int kbps = 192, do_trim = 1;
    const wchar_t* ins[64];
    int nin = 0;
    for (int i = 2; i < argc; i++) {
        if (!wcscmp(argv[i], L"--out") && i + 1 < argc) out = argv[++i];
        else if (!wcscmp(argv[i], L"--lufs") && i + 1 < argc) lufs = _wtof(argv[++i]);
        else if (!wcscmp(argv[i], L"--kbps") && i + 1 < argc) kbps = _wtoi(argv[++i]);
        else if (!wcscmp(argv[i], L"--no-trim")) do_trim = 0;
        else if (nin < 64) ins[nin++] = argv[i];
    }
    if (!out || !nin) die("prepare needs --out and at least one input");
    Pcm mix = {0};
    for (int k = 0; k < nin; k++) {
        Pcm p = {0};
        decode(ins[k], &p);
        to_stereo(&p);
        resample(&p, OUT_RATE);
        if (!mix.d) {
            mix = p;
            continue;
        }
        if (p.n > mix.n) {
            mix.d = xrealloc(mix.d, p.n * 2 * sizeof(float));
            memset(mix.d + mix.n * 2, 0, (p.n - mix.n) * 2 * sizeof(float));
            mix.n = p.n;
        }
        for (size_t i = 0; i < p.n * 2; i++) mix.d[i] += p.d[i];
        free(p.d);
    }
    if (do_trim) trim(&mix);
    double li = integrated_lufs(&mix);
    if (li < -70) die("%s: no audio (silent)", narrow(base_name(ins[0])));
    double gain = lufs - li;
    if (gain > 24) gain = 24;
    if (gain < -30) gain = -30;
    int limited = 0;
    double pk = gain_limit(&mix, gain, &limited);
    encode_mp3(&mix, out, kbps);
    long bytes = strip_tags(out);
    printf("{\"samples\": %zu, \"rate\": %d, \"seconds\": %.3f, \"lufs_in\": %.2f, \"peak_in\": %.2f, "
           "\"gain_db\": %.2f, \"limited\": %s, \"bytes\": %ld}\n",
           mix.n, OUT_RATE, (double)mix.n / OUT_RATE, li, pk, gain, limited ? "true" : "false", bytes);
    MFShutdown();
    return 0;
}
