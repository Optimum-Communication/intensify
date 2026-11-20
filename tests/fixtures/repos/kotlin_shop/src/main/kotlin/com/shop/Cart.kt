package com.shop

import com.shop.payments.Gateway
import com.shop.util.Log as Logger

/** One product in a cart. */
data class Line(val sku: String, val cents: Int, val qty: Int = 1) {
    fun amount(): Int = cents * qty
}

/** Collects lines and prices them. */
class Cart(private val gateway: Gateway, val rules: List<PriceRule> = emptyList()) {
    private val lines = mutableListOf<Line>()

    @Throws(CartError::class)
    fun add(sku: String, cents: Int, qty: Int = 1) {
        if (qty <= 0 || cents < 0) {
            throw CartError("bad line: $sku")
        }
        lines.add(Line(sku, cents, qty))
        Logger.info("added $sku")
    }

    fun add(line: Line) = add(line.sku, line.cents, line.qty)

    fun total(vararg codes: String): Int {
        var total = Pricing.subtotal(lines)
        for (rule in rules) {
            total = rule.apply(total)
        }
        return total
    }

    fun checkout(token: String): String? {
        val amount = total()
        return gateway.charge(amount, token)?.id
    }

    internal fun debug() = lines.size

    private fun secret() = Unit

    companion object {
        fun empty(): Cart = Cart(Gateway.sandbox())
    }
}
