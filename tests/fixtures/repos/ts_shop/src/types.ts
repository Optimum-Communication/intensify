export interface PriceRule {
  apply(cents: number): number;
}
