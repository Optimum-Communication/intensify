package com.shop

fun interface PriceRule {
    fun apply(cents: Int): Int
}

sealed class Discount : PriceRule {
    object None : Discount() {
        override fun apply(cents: Int) = cents
    }
}
