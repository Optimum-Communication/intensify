package pricing

// Rule changes a cart total.
type Rule interface {
	Apply(cents int) int
}

// Named is a rule with a name.
type Named interface {
	Rule
	Name() string
}

// HalfOff takes half off.
type HalfOff struct{}

func (HalfOff) Apply(cents int) int { return cents / 2 }

func (HalfOff) Name() string { return "half off" }

// Cents is an amount of money.
type Cents int

// Round rounds to the nearest five cents.
func Round(cents int) int {
	switch r := cents % 5; {
	case r == 0:
		return cents
	case r < 3:
		return cents - r
	default:
		return cents + 5 - r
	}
}

func roundDown(cents int) int { return cents - cents%5 }

// Map applies f to every amount.
func Map[T any](xs []T, f func(T) T) []T {
	out := make([]T, 0, len(xs))
	for _, x := range xs {
		out = append(out, f(x))
	}
	return out
}
