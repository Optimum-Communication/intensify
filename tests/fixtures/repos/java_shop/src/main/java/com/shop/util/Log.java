package com.shop.util;

public final class Log {
    private Log() {}

    public static void info(String message, Object... args) {
        System.out.println(String.format(message, args));
    }

    public static void warn(String message) {
        System.err.println(message);
    }
}
