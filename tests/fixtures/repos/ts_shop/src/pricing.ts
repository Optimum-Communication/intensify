import type { PriceRule } from "./types";

export function subtotal(items: Array<[string, number]>): number {
  return items.reduce((sum, [, cents]) => sum + cents, 0);
}

export const applyDiscount = (cents: number, code?: string): number =>
  code === "HALF" ? Math.floor(cents / 2) : cents;

export class HalfOff implements PriceRule {
  apply(cents: number): number {
    return applyDiscount(cents, "HALF");
  }
}

function roundCents(n: number) {
  return Math.round(n);
}
