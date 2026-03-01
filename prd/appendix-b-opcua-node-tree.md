# Appendix B: Full OPC-UA Node Tree

Namespace URI: `urn:collatr:factory-simulator`
Namespace index: 2

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
    |   +-- OvenTemperature       (ns=2;s=PackagingLine.Laminator1.OvenTemperature)    Double, C
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

All leaf nodes have the following OPC-UA attributes:
- `AccessLevel`: Read-only (except setpoint nodes which are Read/Write)
- `MinimumSamplingInterval`: Matches the signal's configured sample rate in milliseconds
- `EURange`: Set to the signal's configured min/max range
- `EngineeringUnits`: Set to the signal's unit string
