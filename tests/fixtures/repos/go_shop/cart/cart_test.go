package cart_test

import (
	"testing"

	"example.com/shop/cart"
	"example.com/shop/pricing"
)

func TestTotal(t *testing.T) {
	c := cart.New(pricing.HalfOff{})
	if err := c.Add("apple", 100, 2); err != nil {
		t.Fatal(err)
	}
	if got, _ := c.Total(); got != 100 {
		t.Fatalf("got %d", got)
	}
}
