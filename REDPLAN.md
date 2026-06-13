# Node-Red Dynamic Retailer Comparison - Plan

## Problem with Current Flow
- Hardcoded retailer names in function nodes
- 7 separate HA entity nodes (one per sensor)
- Must edit multiple places to add/remove retailers
- Not maintainable

## Solution: Single Configuration Point

### Architecture

```
[Inject 5min] → [Read CSV] → [Parse Config] → [Calculate Costs] → [Dynamic Sensors]
       ↓                              ↑                                    ↓
[Template: Retailer Config]    [Config from template]            [HA API: Create/Update]
```

### Key Design

1. **Template Node**: Single CSV configuration for ALL retailers
2. **Parse Config Node**: Reads CSV, builds retailer objects dynamically
3. **Calculate Costs Node**: Iterates over retailers from config (no hardcoded names)
4. **Dynamic Sensors Node**: Generates HA service calls for each retailer
5. **HA API Node**: Creates/updates sensors dynamically using `sensor.*` service

### CSV Config Format (Template Node)

```csv
name,model,dsc,sub,off_pk,sh_pk,pk_pk,off_fit,sh_fit,pk_fit,sp_fit,off_s,off_e,pk_s,pk_e,sp_s,sp_e,exp_s,exp_e,exp_rate,base,nw
FlowPower,hybrid,1.3419,0,0,0,0,0,0,0.45,0,0,0,0,0,0,0,17.5,19.5,0.45,0.34,0
Origin Loop Max,fixed_tou,0.003446,0,0.187,0.187,0.539,0.05,0.05,0.22,0,0,0,17,21,0,0,0,0,0,0,0
Globird VPP,fixed_tou,0.003616,0,0,0.363,0.495,0,0,0.05,0.15,11,14,16,23,18,21,0,0,0,0,0
CovaU SolarMax,fixed_tou,0.003562,0,0.2802,0.2802,0.6139,0.05,0.05,0.05,0,11,14,17,21,17,21,0,0,0,0,0
Amber,variable,1.76,25,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.05
```

### CSV Columns

| Column | Description | Example |
|--------|-------------|---------|
| name | Retailer name (used for sensor IDs) | "FlowPower" |
| model | Pricing model: hybrid, fixed_tou, variable | "hybrid" |
| dsc | Daily supply charge ($) | 1.3419 |
| sub | Monthly subscription ($) | 25 |
| off_pk | Offpeak import rate ($/kWh) | 0.187 |
| sh_pk | Shoulder import rate ($/kWh) | 0.187 |
| pk_pk | Peak import rate ($/kWh) | 0.539 |
| off_fit | Offpeak FIT rate ($/kWh) | 0.05 |
| sh_fit | Shoulder FIT rate ($/kWh) | 0.05 |
| pk_fit | Peak FIT rate ($/kWh) | 0.22 |
| sp_fit | Super peak FIT rate ($/kWh) | 0.15 |
| off_s | Offpeak start hour | 11 |
| off_e | Offpeak end hour | 14 |
| pk_s | Peak start hour | 17 |
| pk_e | Peak end hour | 21 |
| sp_s | Super peak start hour | 18 |
| sp_e | Super peak end hour | 21 |
| exp_s | Export window start (hybrid only) | 17.5 |
| exp_e | Export window end (hybrid only) | 19.5 |
| exp_rate | Export window rate (hybrid only) | 0.45 |
| base | Base rate for import (hybrid only) | 0.34 |
| nw | Network cost for variable model | 0.05 |

### Dynamic Calculation Logic

```javascript
// Parse config from template
const retailers = parseConfig(msg.config);

// For each interval in CSV
for (each row) {
    for (each retailer in retailers) {
        // Calculate based on model type
        if (retailer.model === 'fixed_tou') {
            // Use off_pk, sh_pk, pk_pk rates
        } else if (retailer.model === 'variable') {
            // Use AEMO_Price + nw
        } else if (retailer.model === 'hybrid') {
            // Use base rate (simplified)
        }
    }
}
```

### Dynamic Sensor Creation

Use HA API service calls instead of hardcoded entity nodes:

```javascript
// For each retailer
const entity_id = `sensor.retailer_${retailer.name.toLowerCase().replace(/\s+/g, '_')}_daily`;
const value = totals[retailer.name].net;

// Create message for ha-api node
msg.payload = {
    state: value,
    attributes: {
        friendly_name: `${retailer.name} Daily Cost`,
        unit_of_measurement: '$',
        icon: 'mdi:cash'
    }
};
msg.topic = entity_id;
```

### Flow Structure

```
┌─────────────────────────────────────────────────────────┐
│  EnergyRetailerComparison Tab                            │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  [Inject 5min] ──→ [Read CSV] ──→ [Parse Config]       │
│                                          │               │
│                                          ▼               │
│                              [Template: Retailer Config] │
│                                          │               │
│                                          ▼               │
│                              [Calculate Costs]           │
│                                          │               │
│                                          ▼               │
│                              [Dynamic Sensors]           │
│                                          │               │
│                                          ▼               │
│                              [HA API: Create/Update]     │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

### Benefits

1. **Single config point**: Edit CSV in template node to add/remove retailers
2. **Dynamic sensors**: Automatically creates `sensor.retailer_{name}_daily`
3. **No hardcoded logic**: Calculation functions iterate over config
4. **Easy to maintain**: Change rates in one place
5. **Extensible**: Add new retailers without code changes

### Implementation Steps

1. Create template node with retailer CSV config
2. Create "Parse Config" function node
3. Refactor "Calculate Costs" to use parsed config
4. Create "Dynamic Sensors" function node
5. Add HA API node for sensor updates
6. Remove hardcoded HA entity nodes
7. Test with config changes

### Sensor Lifecycle Management

**Orphaned Sensor Cleanup**: When a retailer is removed from config, auto-delete its sensor.

```javascript
// Track config in flow context
const prevConfig = flow.get('retailer_config') || [];
const currConfig = parseConfig(msg.config);

// Find removed retailers
const removed = prevConfig.filter(r => !currConfig.find(c => c.name === r.name));

// Delete orphaned sensors via HA API
for (const r of removed) {
    const entity_id = `sensor.retailer_${r.name.toLowerCase().replace(/\\s+/g, '_')}_daily`;
    // Call HA service: sensor.delete_entity
}

// Save current config for next comparison
flow.set('retailer_config', currConfig);
```

### Benefits

1. **Single config point**: Edit CSV in template node to add/remove retailers
2. **Dynamic sensors**: Automatically creates `sensor.retailer_{name}_daily`
3. **No hardcoded logic**: Calculation functions iterate over config
4. **Easy to maintain**: Change rates in one place
5. **Extensible**: Add new retailers without code changes
6. **Self-cleaning**: Orphaned sensors auto-deleted when retailer removed
