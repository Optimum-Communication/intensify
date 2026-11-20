package com.shop;

import com.shop.payments.Gateway;
import java.util.ArrayList;
import java.util.List;

/** A shopping cart. */
public class Cart {
    private final List<Line> lines = new ArrayList<>();
    private final Gateway gateway;

    public Cart(Gateway gateway) {
        this.gateway = gateway;
    }

    /** Adds a line; quantity must be positive. */
    @Deprecated
    public void add(String sku, int cents, int qty) throws CartError {
        if (qty <= 0 || cents < 0) {
            throw new CartError("bad line: " + sku);
        }
        lines.add(new Line(sku, cents, qty));
        com.shop.util.Log.info("added " + sku);
    }

    public void add(String sku, int cents) throws CartError {
        add(sku, cents, 1);
    }

    public int total(String... codes) {
        int sum = Pricing.subtotal(lines);
        for (String code : codes) {
            sum = Pricing.applyDiscount(sum, code);
        }
        return sum;
    }

    public boolean checkout() {
        return this.gateway.charge(total()) == Gateway.Status.OK;
    }

    private void debug() {}

    /** One line of a cart. */
    public record Line(String sku, int cents, int qty) {
        public int amount() {
            return cents * qty;
        }
    }
}
