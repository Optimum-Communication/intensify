package com.shop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.shop.payments.Gateway;
import org.junit.jupiter.api.Test;

class CartTest {
    @Test
    void totalAppliesDiscount() throws CartError {
        Cart cart = new Cart(new Gateway());
        cart.add("apple", 100, 2);
        assertEquals(100, cart.total("HALF"));
        assertEquals(50, Pricing.applyDiscount(100, "HALF"));
    }
}
