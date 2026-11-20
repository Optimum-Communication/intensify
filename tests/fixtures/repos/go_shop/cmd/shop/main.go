package main

import (
	"context"

	"example.com/shop/cart"
	"example.com/shop/payments"
)

func main() {
	c := cart.New()
	_ = c.Add("apple", 100, 1)
	var g payments.Gateway
	_, _ = g.Charge(context.Background(), c, "tok")
}
