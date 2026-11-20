package com.shop

import com.shop.util.*

object Pricing {
    fun subtotal(lines: List<Line>): Int {
        var sum = 0
        for (line in lines) sum += line.amount()
        return roundCents(sum)
    }

    fun roundCents(cents: Int): Int = when (cents % 5) {
        0 -> cents
        1, 2 -> cents - cents % 5
        else -> cents + 5 - cents % 5
    }
}

internal class HalfOff : PriceRule {
    override fun apply(cents: Int): Int {
        Log.warn("half off")
        return cents / 2
    }
}

/** Extension: total of a cart after half off. */
fun Cart.halfTotal(): Int = HalfOff().apply(total())

enum class Tier { BASIC, GOLD }
