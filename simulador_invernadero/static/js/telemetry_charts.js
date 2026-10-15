/**
 * telemetry_charts.js
 * Gráficos de alta resolución en tiempo real para el Invernadero:
 * 1. Curva de Dryback de VWC (%) y eventos de disparo.
 * 2. Dinámica de Sales / EC (mS/cm).
 * 3. Déficit de Presión de Vapor (VPD kPa) y Microclima.
 */

class TelemetryCharts {
    constructor() {
        this.chartVWC = null;
        this.chartEC = null;
        this.chartVPD = null;
        this.initCharts();
    }

    initCharts() {
        // Estilo oscuro común para Chart.js
        Chart.defaults.color = '#90a4ae';
        Chart.defaults.borderColor = '#1e293b';
        Chart.defaults.font.family = 'Inter, system-ui, sans-serif';

        // 1. Gráfico de VWC (%)
        const ctxVWC = document.getElementById('chartVWC').getContext('2d');
        this.chartVWC = new Chart(ctxVWC, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    {
                        label: 'VWC Sustrato (%)',
                        data: [],
                        borderColor: '#00e5ff',
                        backgroundColor: 'rgba(0, 229, 255, 0.1)',
                        fill: true,
                        tension: 0.2,
                        pointRadius: 0,
                        borderWidth: 2
                    },
                    {
                        label: 'Capacidad de Campo (CC)',
                        data: [],
                        borderColor: 'rgba(255, 215, 0, 0.4)',
                        borderDash: [5, 5],
                        pointRadius: 0,
                        borderWidth: 1.5,
                        fill: false
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                scales: {
                    y: {
                        min: 10,
                        max: 60,
                        title: { display: true, text: 'Humedad (%)', color: '#90a4ae' }
                    },
                    x: {
                        ticks: { maxTicksLimit: 8 }
                    }
                },
                plugins: {
                    legend: { position: 'top', labels: { boxWidth: 12 } }
                }
            }
        });

        // 2. Gráfico de EC (mS/cm)
        const ctxEC = document.getElementById('chartEC').getContext('2d');
        this.chartEC = new Chart(ctxEC, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    {
                        label: 'EC Sustrato',
                        data: [],
                        borderColor: '#ff9100',
                        tension: 0.2,
                        pointRadius: 0,
                        borderWidth: 2
                    },
                    {
                        label: 'EC entrada P1',
                        data: [],
                        borderColor: 'rgba(76, 175, 80, 0.6)',
                        borderDash: [4, 4],
                        pointRadius: 0,
                        borderWidth: 1.5
                    },
                    {
                        label: 'EC entrada P2',
                        data: [],
                        borderColor: 'rgba(33, 150, 243, 0.7)',
                        borderDash: [2, 3],
                        pointRadius: 0,
                        borderWidth: 1.5
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                scales: {
                    y: {
                        min: 0.5,
                        max: 5.5,
                        title: { display: true, text: 'mS/cm', color: '#90a4ae' }
                    },
                    x: {
                        ticks: { maxTicksLimit: 8 }
                    }
                },
                plugins: {
                    legend: { position: 'top', labels: { boxWidth: 12 } }
                }
            }
        });

        // 3. Gráfico de VPD y Temperatura
        const ctxVPD = document.getElementById('chartVPD').getContext('2d');
        this.chartVPD = new Chart(ctxVPD, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    {
                        label: 'VPD (kPa)',
                        data: [],
                        borderColor: '#e040fb',
                        backgroundColor: 'rgba(224, 64, 251, 0.08)',
                        fill: true,
                        tension: 0.2,
                        pointRadius: 0,
                        borderWidth: 2,
                        yAxisID: 'y'
                    },
                    {
                        label: 'Temp (°C)',
                        data: [],
                        borderColor: '#ff5252',
                        pointRadius: 0,
                        borderWidth: 1.5,
                        yAxisID: 'y1'
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                scales: {
                    y: {
                        type: 'linear',
                        position: 'left',
                        min: 0.2,
                        max: 2.2,
                        title: { display: true, text: 'VPD (kPa)', color: '#e040fb' }
                    },
                    y1: {
                        type: 'linear',
                        position: 'right',
                        min: 14,
                        max: 34,
                        grid: { drawOnChartArea: false },
                        title: { display: true, text: 'Temp (°C)', color: '#ff5252' }
                    },
                    x: {
                        ticks: { maxTicksLimit: 8 }
                    }
                },
                plugins: {
                    legend: { position: 'top', labels: { boxWidth: 12 } }
                }
            }
        });
    }

    updateHistory(historyList, environment, vwcCC) {
        if (!historyList || historyList.length === 0) return;

        const labels = historyList.map(pt => `D${pt.day} ${Math.floor(pt.hour)}h`);
        const vwcData = historyList.map(pt => pt.p0_vwc);
        const ccData = historyList.map(() => vwcCC);
        const ecData = historyList.map(pt => pt.p0_ec);
        const p1InputData = historyList.map(pt => pt.p1_input_ec ?? environment.solution_p1.ec);
        const p2InputData = historyList.map(pt => pt.p2_input_ec ?? environment.solution_p2.ec);
        const vpdData = historyList.map(pt => pt.vpd);
        const tempData = historyList.map(pt => pt.temp);

        // Actualizar VWC
        this.chartVWC.data.labels = labels;
        this.chartVWC.data.datasets[0].data = vwcData;
        this.chartVWC.data.datasets[1].data = ccData;
        this.chartVWC.update('none');

        // Actualizar EC
        this.chartEC.data.labels = labels;
        this.chartEC.data.datasets[0].data = ecData;
        this.chartEC.data.datasets[1].data = p1InputData;
        this.chartEC.data.datasets[2].data = p2InputData;
        this.chartEC.update('none');

        // Actualizar VPD
        this.chartVPD.data.labels = labels;
        this.chartVPD.data.datasets[0].data = vpdData;
        this.chartVPD.data.datasets[1].data = tempData;
        this.chartVPD.update('none');
    }
}
