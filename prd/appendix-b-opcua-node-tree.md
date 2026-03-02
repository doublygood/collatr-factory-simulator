# Appendix B: Full OPC-UA Node Tree

Namespace URI: `urn:collatr:factory-simulator`
Namespace index: 2

## Packaging Line

```
Root (i=84)
  Objects (i=85)
    Server (i=2253)
    PackagingLine (ns=2;s=PackagingLine)
    |
    +-- Press1 (ns=2;s=PackagingLine.Press1)
    |   +-- LineSpeed             (ns=2;s=PackagingLine.Press1.LineSpeed)              Double, m/min
    |   +-- WebTension            (ns=2;s=PackagingLine.Press1.WebTension)             Double, N
    |   +-- State                 (ns=2;s=PackagingLine.Press1.State)                  UInt16, enum
    |   +-- FaultCode             (ns=2;s=PackagingLine.Press1.FaultCode)              UInt16
    |   +-- ImpressionCount       (ns=2;s=PackagingLine.Press1.ImpressionCount)        UInt32
    |   +-- GoodCount             (ns=2;s=PackagingLine.Press1.GoodCount)              UInt32
    |   +-- WasteCount            (ns=2;s=PackagingLine.Press1.WasteCount)             UInt32
    |   +-- Registration
    |   |   +-- ErrorX            (ns=2;s=PackagingLine.Press1.Registration.ErrorX)    Double, mm
    |   |   +-- ErrorY            (ns=2;s=PackagingLine.Press1.Registration.ErrorY)    Double, mm
    |   +-- Ink
    |   |   +-- Viscosity         (ns=2;s=PackagingLine.Press1.Ink.Viscosity)          Double, seconds
    |   |   +-- Temperature       (ns=2;s=PackagingLine.Press1.Ink.Temperature)        Double, C
    |   +-- Dryer
    |   |   +-- Zone1
    |   |   |   +-- Temperature   (ns=2;s=PackagingLine.Press1.Dryer.Zone1.Temperature) Double, C
    |   |   |   +-- Setpoint      (ns=2;s=PackagingLine.Press1.Dryer.Zone1.Setpoint)    Double, C
    |   |   +-- Zone2
    |   |   |   +-- Temperature   (ns=2;s=PackagingLine.Press1.Dryer.Zone2.Temperature) Double, C
    |   |   |   +-- Setpoint      (ns=2;s=PackagingLine.Press1.Dryer.Zone2.Setpoint)    Double, C
    |   |   +-- Zone3
    |   |       +-- Temperature   (ns=2;s=PackagingLine.Press1.Dryer.Zone3.Temperature) Double, C
    |   |       +-- Setpoint      (ns=2;s=PackagingLine.Press1.Dryer.Zone3.Setpoint)    Double, C
    |   +-- MainDrive
    |   |   +-- Current           (ns=2;s=PackagingLine.Press1.MainDrive.Current)      Double, A
    |   |   +-- Speed             (ns=2;s=PackagingLine.Press1.MainDrive.Speed)        Double, RPM
    |   +-- NipPressure           (ns=2;s=PackagingLine.Press1.NipPressure)            Double, bar
    |   +-- Unwind
    |   |   +-- Diameter          (ns=2;s=PackagingLine.Press1.Unwind.Diameter)        Double, mm
    |   +-- Rewind
    |       +-- Diameter          (ns=2;s=PackagingLine.Press1.Rewind.Diameter)        Double, mm
    |
    +-- Laminator1 (ns=2;s=PackagingLine.Laminator1)
    |   +-- NipTemperature        (ns=2;s=PackagingLine.Laminator1.NipTemperature)     Double, C
    |   +-- NipPressure           (ns=2;s=PackagingLine.Laminator1.NipPressure)        Double, bar
    |   +-- TunnelTemperature     (ns=2;s=PackagingLine.Laminator1.TunnelTemperature)  Double, C
    |   +-- WebSpeed              (ns=2;s=PackagingLine.Laminator1.WebSpeed)           Double, m/min
    |   +-- AdhesiveWeight        (ns=2;s=PackagingLine.Laminator1.AdhesiveWeight)     Double, g/m2
    |
    +-- Slitter1 (ns=2;s=PackagingLine.Slitter1)
    |   +-- Speed                 (ns=2;s=PackagingLine.Slitter1.Speed)                Double, m/min
    |   +-- WebTension            (ns=2;s=PackagingLine.Slitter1.WebTension)           Double, N
    |   +-- ReelCount             (ns=2;s=PackagingLine.Slitter1.ReelCount)            UInt32
    |
    +-- Energy (ns=2;s=PackagingLine.Energy)
        +-- LinePower             (ns=2;s=PackagingLine.Energy.LinePower)              Double, kW
        +-- CumulativeKwh         (ns=2;s=PackagingLine.Energy.CumulativeKwh)          Double, kWh
```

## Food & Beverage Line

```
    FoodBevLine (ns=2;s=FoodBevLine)
    |
    +-- Mixer1 (ns=2;s=FoodBevLine.Mixer1)
    |   +-- State                 (ns=2;s=FoodBevLine.Mixer1.State)                    UInt16, enum
    |   +-- BatchId               (ns=2;s=FoodBevLine.Mixer1.BatchId)                  String
    |
    +-- Oven1 (ns=2;s=FoodBevLine.Oven1)
    |   +-- State                 (ns=2;s=FoodBevLine.Oven1.State)                     UInt16, enum
    |
    +-- Filler1 (ns=2;s=FoodBevLine.Filler1)
    |   +-- LineSpeed             (ns=2;s=FoodBevLine.Filler1.LineSpeed)               Double, packs/min
    |   +-- FillWeight            (ns=2;s=FoodBevLine.Filler1.FillWeight)              Double, g
    |   +-- FillTarget            (ns=2;s=FoodBevLine.Filler1.FillTarget)              Double, g
    |   +-- FillDeviation         (ns=2;s=FoodBevLine.Filler1.FillDeviation)           Double, g
    |   +-- PacksProduced         (ns=2;s=FoodBevLine.Filler1.PacksProduced)           UInt32
    |   +-- RejectCount           (ns=2;s=FoodBevLine.Filler1.RejectCount)             UInt32
    |   +-- State                 (ns=2;s=FoodBevLine.Filler1.State)                   UInt16, enum
    |
    +-- QC1 (ns=2;s=FoodBevLine.QC1)
    |   +-- ActualWeight          (ns=2;s=FoodBevLine.QC1.ActualWeight)                Double, g
    |   +-- OverweightCount       (ns=2;s=FoodBevLine.QC1.OverweightCount)             UInt32
    |   +-- UnderweightCount      (ns=2;s=FoodBevLine.QC1.UnderweightCount)            UInt32
    |   +-- MetalDetectTrips      (ns=2;s=FoodBevLine.QC1.MetalDetectTrips)            UInt32
    |   +-- Throughput            (ns=2;s=FoodBevLine.QC1.Throughput)                  Double, items/min
    |   +-- RejectTotal           (ns=2;s=FoodBevLine.QC1.RejectTotal)                 UInt32
    |
    +-- CIP1 (ns=2;s=FoodBevLine.CIP1)
    |   +-- State                 (ns=2;s=FoodBevLine.CIP1.State)                      UInt16, enum
    |
    +-- Energy (ns=2;s=FoodBevLine.Energy)
        +-- LinePower             (ns=2;s=FoodBevLine.Energy.LinePower)                Double, kW
        +-- CumulativeKwh         (ns=2;s=FoodBevLine.Energy.CumulativeKwh)            Double, kWh
```

> `FoodBevLine` sits as a sibling of `PackagingLine` under the `Objects` folder. Both root nodes are always present in the address space. Only the active profile's nodes publish changing values; the inactive profile's nodes report StatusCode.BadNotReadable with AccessLevel set to 0 (see Section 3.2.1).

## OPC-UA Attribute Conventions

All leaf nodes across both profiles have the following OPC-UA attributes:

- `AccessLevel`: Read-only (except setpoint nodes which are Read/Write)
- `MinimumSamplingInterval`: Matches the signal's configured sample rate in milliseconds
- `EURange`: Set to the signal's configured min/max range
- `EngineeringUnits`: Set to the signal's unit string

**State enum nodes** (`*.State`) use `UInt16` data type. The `EnumStrings` property (listing valid state names as `LocalizedText[]`) SHOULD be set if asyncua supports it cleanly. If asyncua's `EnumStrings` support proves problematic, integer values alone are sufficient for MVP. Budget 0.5 days investigation in Phase 2. See the equipment sections in [02-simulated-factory-layout.md](02-simulated-factory-layout.md) and [02b-factory-layout-food-and-beverage.md](02b-factory-layout-food-and-beverage.md) for enum definitions.

**Counter nodes** (`PacksProduced`, `RejectCount`, `OverweightCount`, `UnderweightCount`, `MetalDetectTrips`, `RejectTotal`) use `UInt32` data type and increment monotonically. They reset to 0 on shift change or via configuration. (OPC-UA method nodes such as `ResetCounters` are deferred to post-MVP.)

**String nodes** (`BatchId`) use OPC-UA `String` data type. Value changes are event-driven (published when a new batch starts).
