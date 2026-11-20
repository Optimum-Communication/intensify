package com.shop;

import static com.shop.util.Log.warn;

import java.util.List;

public final class Pricing {
    private Pricing() {}

    public static int subtotal(List<Cart.Line> lines) {
        int sum = 0;
        for (Cart.Line line : lines) {
            sum += line.amount();
        }
        return roundCents(sum);
    }

    public static int applyDiscount(int cents, String code) {
        PriceRule rule = "HALF".equals(code) ? new HalfOff() : null;
        if (rule == null) {
            warn("unknown code " + code);
            return cents;
        }
        return rule.apply(cents);
    }

    static int roundCents(int cents) {
        return Math.max(0, cents);
    }
}

class HalfOff implements PriceRule {
    @Override
    public int apply(int cents) {
        return Pricing.roundCents(cents / 2);
    }
}
