## v0.5.3 (2024-03-23)

### Fix

- **Strategy**: fix duplicate order cancellation for range bot
- **Strategy**: range bot will actively prune stale orders

## v0.5.2 (2024-03-23)

### Fix

- **Strategy**: range bot has maximum orders to place at one time
- **Strategy**: type conversion for no_levels param in range bot

## v0.5.1 (2024-03-23)

### Fix

- create home directory on first use

## v0.5.0 (2024-03-23)

### Feat

- **CLI**: improve exchange api key management
- **CLI**: added project management

## v0.4.0 (2024-03-22)

### Feat

- support adding user-defined projects for custom bot deployment
- **Strategy**: improve breakout strategy

## v0.3.0 (2024-03-21)

### Feat

- **Strategy**: added range bonud grid strategy
- **Strategy**: breakout strategy will adjust stop loss size to close position in profit
- **Strategy**: added breakout trend following strategy

### Fix

- **Strategy**: decimal conversion errors

## v0.2.1 (2024-03-13)

### Fix

- **Strategy**: fixed delta calculation for negative amounts in cash and carry bot

## v0.2.0 (2024-03-12)

### Feat

- **Strategy**: improve cash and carry order price calculation to prevent hanging orders
- **Strategy**: added cash and carry bot

### Fix

- **Strategy**: cash and carry bot will adjust deltas by min amount limit to prevent bad orders
- **Strategy**: fixed bad_start attribute error for cash and carry bot

## v0.1.0 (2024-03-10)

### Feat

- **cryptobots**: created cryptobots project
