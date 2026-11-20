package com.shop

import jakarta.persistence.Entity
import org.springframework.data.repository.CrudRepository

@Entity
class StockLevel(val productId: Long, val qty: Int)

interface StockRepository : CrudRepository<StockLevel, Long>

class Warehouse(private val stock: StockRepository) {
    fun levels(): List<StockLevel> = stock.findAll().toList()

    fun receive(level: StockLevel) {
        stock.save(level)
    }
}
