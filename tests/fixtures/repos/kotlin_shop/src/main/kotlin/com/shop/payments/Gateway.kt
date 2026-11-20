package com.shop.payments

import com.shop.util.Log

data class Receipt(val id: String)

open class Gateway(private val retries: Int = 0) {
    fun charge(cents: Int, token: String): Receipt? {
        for (i in 0..retries) {
            val receipt = send(cents, token) ?: continue
            Log.info("charged")
            return receipt
        }
        return null
    }

    protected open fun send(cents: Int, token: String): Receipt? =
        if (cents > 0 && token.isNotEmpty()) Receipt(token) else null

    companion object {
        fun sandbox() = Gateway()
    }
}
