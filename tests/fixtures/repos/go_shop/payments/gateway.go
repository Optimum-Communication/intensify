package payments

import (
	"context"
	"errors"

	"example.com/shop/cart"
)

// ErrDeclined is returned when the card is declined.
var ErrDeclined = errors.New("declined")

// Gateway charges cards.
type Gateway struct {
	retries int
}

// Charge bills the cart total.
func (g *Gateway) Charge(ctx context.Context, c *cart.Cart, token string) (string, error) {
	if c.Empty() {
		return "", ErrDeclined
	}
	total, err := c.Total()
	if err != nil {
		return "", err
	}
	for i := 0; i <= g.retries; i++ {
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		default:
		}
		if id, ok := g.try(total, token); ok {
			return id, nil
		}
	}
	return "", ErrDeclined
}

func (g *Gateway) try(cents int, token string) (string, bool) {
	return token, cents > 0
}
