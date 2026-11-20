package com.shop

import com.shop.payments.Gateway
import kotlin.test.Test
import kotlin.test.assertEquals

class CartTest {
    @Test
    fun total() {
        val cart = Cart(Gateway())
        cart.add("apple", 100, 2)
        assertEquals(100, cart.halfTotal())
    }
}
