// 每小时把 machine_metrics 的原始点降采样为小时均值 → metrics_hourly（无限留存）
// 幂等：每次跑最近 2h，窗口按整点截断，重复写同 series+ts 会被覆盖
option task = {name: "downsample-hourly", every: 1h, offset: 5m}

from(bucket: "machine_metrics")
    |> range(start: -2h)
    |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
    |> to(bucket: "metrics_hourly", org: "monitor-org")
