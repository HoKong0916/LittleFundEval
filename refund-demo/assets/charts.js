(function() {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var DATA = {"curve_x": ["≤7天", "≤15天", "≤30天", "≤60天", "≤90天", "≤120天", "≤150天"], "curve_all": [0.0706, 0.2923, 0.5921, 0.8229, 0.9327, 0.9841, 1.0], "curve_app": [0.075, 0.513, 0.8978, 0.9929, 1.0, 1.0, 1.0], "curve_3c": [0.0637, 0.1415, 0.445, 0.7413, 0.9012, 0.9776, 1.0], "kpi_x": ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"], "kpi_v": [0.04, 0.0446, 0.0356, 0.0518, 0.0536, 0.0608, 0.0452, 0.0554, 0.0565, 0.06, 0.0701, 0.0687, 0.063], "cohort_v": [0.0486, 0.0445, 0.0525, 0.0512, 0.049, 0.05, 0.0588, 0.0567, 0.0594, 0.0609, 0.0688, 0.071, 0.0661], "site_x": ["US", "DE", "UK", "AU"], "site_logi": [0.0061, 0.0053, 0.0062, 0.005], "site_comm": [0.0279, 0.0366, 0.0275, 0.0295], "sup_x": ["S1", "S2", "S3", "S4"], "sup_r30": [0.0233, 0.0399, 0.0411, 0.0285], "sup_fin": [0.0394, 0.0674, 0.0694, 0.0482], "pareto_y": ["E3·3C电子", "E2·3C电子", "E4·3C电子", "E5·3C电子", "E6·3C电子", "H3·家居", "E1·3C电子", "F5·服饰", "H1·家居", "H4·家居"], "pareto_v": [169132, 69341, 66554, 52891, 46198, 38109, 35748, 22946, 19881, 19480], "pareto_sup": ["S2", "S2", "S2", "S3", "S3", "S3", "S1", "S3", "S1", "S3"], "pareto_rate": ["6.8%", "6.5%", "6.3%", "4.7%", "4.9%", "5.3%", "3.1%", "5.3%", "3.6%", "5.5%"], "pareto_info": [{"pid": "E3", "cat": "3C电子", "sup": "S2", "refund": "$16.9万", "rate": "6.82%", "reason": "质量缺陷/无法使用"}, {"pid": "E2", "cat": "3C电子", "sup": "S2", "refund": "$6.9万", "rate": "6.51%", "reason": "质量缺陷/无法使用"}, {"pid": "E4", "cat": "3C电子", "sup": "S2", "refund": "$6.7万", "rate": "6.28%", "reason": "质量缺陷/无法使用"}, {"pid": "E5", "cat": "3C电子", "sup": "S3", "refund": "$5.3万", "rate": "4.68%", "reason": "质量缺陷/无法使用"}, {"pid": "E6", "cat": "3C电子", "sup": "S3", "refund": "$4.6万", "rate": "4.88%", "reason": "质量缺陷/无法使用"}, {"pid": "H3", "cat": "家居", "sup": "S3", "refund": "$3.8万", "rate": "5.35%", "reason": "质量缺陷"}, {"pid": "E1", "cat": "3C电子", "sup": "S1", "refund": "$3.6万", "rate": "3.13%", "reason": "质量缺陷/无法使用"}, {"pid": "F5", "cat": "服饰", "sup": "S3", "refund": "$2.3万", "rate": "5.25%", "reason": "尺码不合"}, {"pid": "H1", "cat": "家居", "sup": "S1", "refund": "$2.0万", "rate": "3.62%", "reason": "质量缺陷"}, {"pid": "H4", "cat": "家居", "sup": "S3", "refund": "$1.9万", "rate": "5.50%", "reason": "质量缺陷"}], "sla_x": ["1-2天", "3天", "4天", "5天", "6-7天", "8-17天"], "sla_v": [0.014, 0.0188, 0.0265, 0.0365, 0.0531, 0.0631], "sla_n": [51913, 27078, 18068, 11237, 8173, 1205]};
  function mk(id, opt) {
    var el = document.getElementById(id);
    if (!el) return;
    var c = echarts.init(el, null, { renderer: 'svg' });
    opt.animation = false;
    opt.textStyle = { color: ink, fontFamily: 'GeistMono, WorkSans, sans-serif' };
    c.setOption(opt);
    window.addEventListener('resize', function() { c.resize(); });
  }
  mk('chart-realization', {
    color: [accent, accent2, muted],
    tooltip: { trigger: 'axis', appendToBody: true, formatter: function(ps) {
      return ps.map(function(p){ return p.marker + p.seriesName + ': ' + (p.value*100).toFixed(1) + '%'; }).join('<br>');
    }},
    legend: { data: ['总体','服饰','3C电子'], top: 0 },
    grid: { left: 56, right: 30, top: 40, bottom: 40 },
    xAxis: { type: 'category', data: DATA.curve_x, axisLine: { lineStyle: { color: rule } }, axisLabel: { color: muted } },
    yAxis: { type: 'value', max: 1, axisLabel: { color: muted, formatter: function(v){ return (v*100)+'%'; } }, splitLine: { lineStyle: { color: rule } } },
    series: [
      { name: '总体', type: 'line', data: DATA.curve_all, symbolSize: 7,
        label: { show: true, color: ink, fontWeight: 600, formatter: function(p){ return p.dataIndex===2 ? (p.value*100).toFixed(0)+'%' : ''; } } },
      { name: '服饰', type: 'line', data: DATA.curve_app, symbolSize: 5 },
      { name: '3C电子', type: 'line', data: DATA.curve_3c, symbolSize: 5 }
    ]
  });
  mk('chart-kpi', {
    color: [accent, accent2],
    tooltip: { trigger: 'axis', appendToBody: true, formatter: function(ps) {
      return ps[0].axisValue + '<br>' + ps.map(function(p){ return p.marker + p.seriesName + ': ' + (p.value*100).toFixed(2) + '%'; }).join('<br>');
    }},
    legend: { data: ['客服月度KPI', '发货队列终率'], top: 0 },
    grid: { left: 56, right: 30, top: 40, bottom: 70 },
    xAxis: { type: 'category', data: DATA.kpi_x, axisLabel: { color: muted, rotate: 45 }, axisLine: { lineStyle: { color: rule } } },
    yAxis: { type: 'value', axisLabel: { color: muted, formatter: function(v){ return (v*100).toFixed(0)+'%'; } }, splitLine: { lineStyle: { color: rule } } },
    series: [
      { name: '客服月度KPI', type: 'line', data: DATA.kpi_v, symbolSize: 6, lineStyle: { width: 2.5 },
        markPoint: { symbolSize: 52, itemStyle: { color: accent }, label: { color: '#fff', fontSize: 10, formatter: function(p){ return p.name; } },
          data: [{ type: 'min', name: '12月·黑五分母虚低' }, { coord: ['2026-01', DATA.kpi_v[3]], name: '1月·黑五退款残留' }] } },
      { name: '发货队列终率', type: 'line', data: DATA.cohort_v, symbolSize: 6, lineStyle: { width: 2.5, type: 'dashed' } }
    ]
  });
  mk('chart-site', {
    color: [accent, accent2],
    tooltip: { trigger: 'axis', appendToBody: true, formatter: function(ps) {
      return ps[0].axisValue + '<br>' + ps.map(function(p){ return p.marker + p.seriesName + ': ' + (p.value*100).toFixed(2) + '%'; }).join('<br>');
    }},
    legend: { data: ['0-7天 物流型退款', '7-30天 商品型退款'], top: 0 },
    grid: { left: 56, right: 30, top: 40, bottom: 40 },
    xAxis: { type: 'category', data: DATA.site_x, axisLabel: { color: muted }, axisLine: { lineStyle: { color: rule } } },
    yAxis: { type: 'value', axisLabel: { color: muted, formatter: function(v){ return (v*100).toFixed(1)+'%'; } }, splitLine: { lineStyle: { color: rule } } },
    series: [
      { name: '0-7天 物流型退款', type: 'bar', data: DATA.site_logi, barWidth: 26, label: { show: true, position: 'top', color: muted, formatter: function(p){ return (p.value*100).toFixed(2)+'%'; } } },
      { name: '7-30天 商品型退款', type: 'bar', data: DATA.site_comm, barWidth: 26, label: { show: true, position: 'top', color: muted, formatter: function(p){ return (p.value*100).toFixed(2)+'%'; } } }
    ]
  });
  mk('chart-supplier', {
    color: [accent, accent2],
    tooltip: { trigger: 'axis', appendToBody: true, formatter: function(ps) {
      return ps[0].axisValue + '<br>' + ps.map(function(p){ return p.marker + p.seriesName + ': ' + (p.value*100).toFixed(2) + '%'; }).join('<br>');
    }},
    legend: { data: ['30天退款率', '外推终率'], top: 0 },
    grid: { left: 56, right: 30, top: 40, bottom: 40 },
    xAxis: { type: 'category', data: DATA.sup_x, axisLabel: { color: muted }, axisLine: { lineStyle: { color: rule } } },
    yAxis: { type: 'value', axisLabel: { color: muted, formatter: function(v){ return (v*100).toFixed(0)+'%'; } }, splitLine: { lineStyle: { color: rule } } },
    series: [
      { name: '30天退款率', type: 'bar', data: DATA.sup_r30, barWidth: 26, label: { show: true, position: 'top', color: muted, formatter: function(p){ return (p.value*100).toFixed(1)+'%'; } } },
      { name: '外推终率', type: 'bar', data: DATA.sup_fin, barWidth: 26, label: { show: true, position: 'top', color: muted, formatter: function(p){ return (p.value*100).toFixed(1)+'%'; } } }
    ]
  });
  mk('chart-pareto', {
    color: [accent],
    tooltip: { trigger: 'axis', appendToBody: true, formatter: function(ps) {
      var p = ps[0];
      var d = DATA.pareto_info[p.dataIndex];
      return d.pid + ' · ' + d.cat + ' · ' + d.sup + '<br>退款金额: ' + d.refund + '<br>退款率: ' + d.rate + '<br>主因: ' + d.reason;
    }},
    grid: { left: 70, right: 120, top: 20, bottom: 40 },
    xAxis: { type: 'value', axisLabel: { color: muted, formatter: function(v){ return (v/1e4).toFixed(0)+'万'; } }, splitLine: { lineStyle: { color: rule } } },
    yAxis: { type: 'category', inverse: true, data: DATA.pareto_y, axisLabel: { color: ink }, axisLine: { lineStyle: { color: rule } } },
    series: [
      { type: 'bar', data: DATA.pareto_v, barWidth: 16,
        itemStyle: { color: function(p) { return DATA.pareto_sup[p.dataIndex]==='S2' ? accent2 : accent; } },
        label: { show: true, position: 'right', color: muted, formatter: function(p){ return (p.value/1e4).toFixed(1)+'万 · ' + DATA.pareto_rate[p.dataIndex]; } } }
    ]
  });
  mk('chart-sla', {
    color: [accent],
    tooltip: { trigger: 'axis', appendToBody: true, formatter: function(ps) {
      var p = ps[0];
      return '发货时效 ' + p.axisValue + '<br>取消率: ' + (p.value*100).toFixed(2) + '%<br>订单量: ' + DATA.sla_n[p.dataIndex].toLocaleString();
    }},
    grid: { left: 56, right: 40, top: 20, bottom: 40 },
    xAxis: { type: 'category', data: DATA.sla_x, axisLabel: { color: muted }, axisLine: { lineStyle: { color: rule } } },
    yAxis: { type: 'value', axisLabel: { color: muted, formatter: function(v){ return (v*100).toFixed(1)+'%'; } }, splitLine: { lineStyle: { color: rule } } },
    series: [
      { type: 'bar', data: DATA.sla_v, barWidth: 34, label: { show: true, position: 'top', color: muted, formatter: function(p){ return (p.value*100).toFixed(2)+'%'; } } }
    ]
  });
})();
