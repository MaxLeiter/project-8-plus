# embed.cmake -- cmake -DIN=<file> -DOUT=<header> -DSYM=<name> -P embed.cmake
# Writes `static const unsigned char SYM[] = {...};` (no terminating NUL; use sizeof).
file(READ "${IN}" _hex HEX)
string(REGEX REPLACE "([0-9a-f][0-9a-f])" "0x\\1," _arr "${_hex}")
string(REGEX REPLACE "((0x..,){16})" "\\1\n" _arr "${_arr}")
file(WRITE "${OUT}" "/* generated from ${IN} -- do not edit */\nstatic const unsigned char ${SYM}[] = {\n${_arr}\n};\n")
