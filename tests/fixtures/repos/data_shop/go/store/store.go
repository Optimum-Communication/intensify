package store

import (
	"database/sql"

	"gorm.io/gorm"
)

// Payment is a GORM model, stored in the payments table.
type Payment struct {
	gorm.Model
	Cents int
}

// Ledger has an explicit table name.
type Ledger struct{ ID int }

func (Ledger) TableName() string { return "ledger_entries" }

const insertLedger = `INSERT INTO ledger_entries (id) VALUES ($1)`

// Record stores a payment and its ledger entry.
func Record(db *sql.DB, g *gorm.DB, id int) error {
	if err := g.Create(&Payment{Cents: id}).Error; err != nil {
		return err
	}
	_, err := db.Exec(insertLedger, id)
	return err
}

// Total sums all payments.
func Total(db *sql.DB) (int, error) {
	var total int
	err := db.QueryRow("SELECT SUM(cents) FROM payments").Scan(&total)
	return total, err
}
