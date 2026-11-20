// Package cart holds a shopping cart.
package cart

import (
	"fmt"

	"example.com/shop/internal/log"
	pr "example.com/shop/pricing"
)

// Line is one product in a cart.
type Line struct {
	SKU   string
	Cents int
	Qty   int
}

// Cart collects lines and prices them.
type Cart struct {
	lines []Line
	rules []pr.Rule
	log   *log.Logger
}

// New returns an empty cart.
func New(rules ...pr.Rule) *Cart {
	return &Cart{rules: rules, log: log.New("cart")}
}

// Add puts a product in the cart.
func (c *Cart) Add(sku string, cents, qty int) error {
	if qty <= 0 || cents < 0 {
		return &Error{SKU: sku}
	}
	c.lines = append(c.lines, Line{SKU: sku, Cents: cents, Qty: qty})
	c.log.Info(fmt.Sprintf("added %s", sku))
	return nil
}

// Total prices the cart after every rule.
func (c *Cart) Total() (int, error) {
	total := 0
	for _, l := range c.lines {
		total += l.Cents * l.Qty
	}
	for _, rule := range c.rules {
		total = rule.Apply(total)
	}
	return pr.Round(total), nil
}

func (c *Cart) debug() string {
	return fmt.Sprint(len(c.lines))
}
