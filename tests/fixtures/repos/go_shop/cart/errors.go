package cart

// Error reports an invalid cart line.
type Error struct {
	SKU string
}

func (e *Error) Error() string {
	return "invalid line: " + e.SKU
}

// Empty reports whether the cart has no lines. It lives in a different file from Cart.
func (c *Cart) Empty() bool {
	return len(c.lines) == 0
}
