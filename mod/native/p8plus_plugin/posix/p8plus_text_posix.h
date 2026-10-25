/* Project 8 Plus (Linux .so): text through stb_truetype -- see p8plus_text_posix.c */
#ifndef P8PLUS_TEXT_POSIX_H
#define P8PLUS_TEXT_POSIX_H

enum { P8T_KB_BOLD, P8T_KB_COND, P8T_FL_HEAD, P8T_FL_BOLD, P8T_FL_SEMI, P8T_FL_BODY };

/* utf8 at em pixels per em in a font of `role`: white-on-black RGBA (coverage in green), W = advance + em,
 * H = font box + em, text at (em/2, em/2 + ascent). 1 = drawn (wh = W, H), 0 = no font, < 0 = -(bytes needed). */
int p8_posix_text(const char* utf8, int em, int role, unsigned char* out, int cap, int* wh);
/* the browser build's p8_js_text_rgba contract (p8plus_kbglyphs.c) */
int p8_posix_text_rgba(const char* utf8, int em, int condensed, unsigned char* out, int cap, int* wh);

#endif
