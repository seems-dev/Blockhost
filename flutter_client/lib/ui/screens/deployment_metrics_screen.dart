import 'dart:async';
import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';
import '../../state/app_state.dart';
import '../../models/deployment_models.dart';
import '../widgets/glass_card.dart';

class DeploymentMetricsScreen extends StatefulWidget {
  const DeploymentMetricsScreen({super.key, required this.state, required this.deployment});
  
  final AppState state;
  final AppDeployment deployment;

  @override
  State<DeploymentMetricsScreen> createState() => _DeploymentMetricsScreenState();
}

class _DeploymentMetricsScreenState extends State<DeploymentMetricsScreen> {
  Timer? _timer;
  bool _isLoading = true;
  String? _error;
  
  // Data buffers (sliding window)
  final int _maxPoints = 20;
  final List<FlSpot> _cpuSpots = [];
  final List<FlSpot> _memSpots = [];
  
  double _timeX = 0;

  Map<String, dynamic>? _latestData;

  @override
  void initState() {
    super.initState();
    _fetchData();
    _timer = Timer.periodic(const Duration(seconds: 3), (timer) {
      _fetchData();
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _fetchData() async {
    try {
      final data = await widget.state.api.getDeploymentMetrics(widget.deployment.id);
      if (!mounted) return;
      
      setState(() {
        _isLoading = false;
        _latestData = data;
        
        if (data['running'] == true) {
          _error = null;
          final double cpu = (data['cpu_percent'] as num?)?.toDouble() ?? 0.0;
          final double mem = (data['memory_usage_mb'] as num?)?.toDouble() ?? 0.0;
          
          _cpuSpots.add(FlSpot(_timeX, cpu));
          _memSpots.add(FlSpot(_timeX, mem));
          
          if (_cpuSpots.length > _maxPoints) _cpuSpots.removeAt(0);
          if (_memSpots.length > _maxPoints) _memSpots.removeAt(0);
          
          _timeX += 3; // 3 seconds interval
        } else {
          _error = data['error'] ?? 'Container is not running.';
        }
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e.toString();
          _isLoading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [Color(0xFF0F172A), Color(0xFF1E293B)],
          ),
        ),
        child: SafeArea(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // Header
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
                child: Row(
                  children: [
                    IconButton(
                      icon: const Icon(Icons.arrow_back_ios, color: Colors.white70, size: 20),
                      onPressed: () => Navigator.of(context).pop(),
                    ),
                    const Text('Live Metrics', style: TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.bold)),
                    const Spacer(),
                    if (!_isLoading && _latestData?['running'] == true)
                      Row(
                        children: [
                          Container(
                            width: 8,
                            height: 8,
                            decoration: const BoxDecoration(
                              color: Colors.greenAccent,
                              shape: BoxShape.circle,
                            ),
                          ),
                          const SizedBox(width: 8),
                          const Text('Live', style: TextStyle(color: Colors.greenAccent, fontSize: 12, fontWeight: FontWeight.bold)),
                        ],
                      )
                  ],
                ),
              ),
              
              if (_isLoading && _cpuSpots.isEmpty)
                const Expanded(child: Center(child: CircularProgressIndicator(color: Color(0xFF06B6D4))))
              else if (_error != null && _cpuSpots.isEmpty)
                Expanded(child: Center(child: Text(_error!, style: const TextStyle(color: Colors.white54))))
              else
                Expanded(
                  child: ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      _buildSummaryCards(),
                      const SizedBox(height: 24),
                      _buildCpuChart(),
                      const SizedBox(height: 24),
                      _buildMemoryChart(),
                      const SizedBox(height: 24),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildSummaryCards() {
    final cpu = (_latestData?['cpu_percent'] as num?)?.toDouble() ?? 0.0;
    final mem = (_latestData?['memory_usage_mb'] as num?)?.toInt() ?? 0;
    final rx = (_latestData?['network_rx_bytes'] as num?)?.toInt() ?? 0;
    
    return Row(
      children: [
        Expanded(
          child: GlassCard(
            child: Padding(
              padding: const EdgeInsets.all(16.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('CPU', style: TextStyle(color: Colors.white54, fontSize: 12)),
                  const SizedBox(height: 4),
                  Text('${cpu.toStringAsFixed(1)}%', style: const TextStyle(color: Color(0xFF06B6D4), fontSize: 24, fontWeight: FontWeight.bold)),
                ],
              ),
            ),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: GlassCard(
            child: Padding(
              padding: const EdgeInsets.all(16.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('RAM', style: TextStyle(color: Colors.white54, fontSize: 12)),
                  const SizedBox(height: 4),
                  Text('${mem}MB', style: const TextStyle(color: Colors.purpleAccent, fontSize: 24, fontWeight: FontWeight.bold)),
                ],
              ),
            ),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: GlassCard(
            child: Padding(
              padding: const EdgeInsets.all(16.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('NET I/O', style: TextStyle(color: Colors.white54, fontSize: 12)),
                  const SizedBox(height: 4),
                  Text('${(rx/1024).toStringAsFixed(0)}KB', style: const TextStyle(color: Colors.greenAccent, fontSize: 18, fontWeight: FontWeight.bold)),
                ],
              ),
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildCpuChart() {
    double minX = _cpuSpots.isEmpty ? 0 : _cpuSpots.first.x;
    double maxX = _cpuSpots.isEmpty ? 60 : _cpuSpots.last.x;
    
    return GlassCard(
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('CPU Usage', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
            const SizedBox(height: 16),
            SizedBox(
              height: 200,
              child: LineChart(
                LineChartData(
                  minX: minX,
                  maxX: maxX,
                  minY: 0,
                  maxY: 100,
                  titlesData: FlTitlesData(
                    show: true,
                    topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                    rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                    bottomTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                    leftTitles: AxisTitles(
                      sideTitles: SideTitles(
                        showTitles: true,
                        reservedSize: 40,
                        getTitlesWidget: (v, meta) => Text('${v.toInt()}%', style: const TextStyle(color: Colors.white30, fontSize: 10)),
                      ),
                    ),
                  ),
                  gridData: FlGridData(
                    show: true,
                    drawVerticalLine: false,
                    getDrawingHorizontalLine: (value) => FlLine(color: Colors.white.withOpacity(0.05), strokeWidth: 1),
                  ),
                  borderData: FlBorderData(show: false),
                  lineBarsData: [
                    LineChartBarData(
                      spots: _cpuSpots,
                      isCurved: true,
                      color: const Color(0xFF06B6D4),
                      barWidth: 3,
                      isStrokeCapRound: true,
                      dotData: const FlDotData(show: false),
                      belowBarData: BarAreaData(
                        show: true,
                        gradient: LinearGradient(
                          colors: [
                            const Color(0xFF06B6D4).withOpacity(0.3),
                            const Color(0xFF06B6D4).withOpacity(0.0),
                          ],
                          begin: Alignment.topCenter,
                          end: Alignment.bottomCenter,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildMemoryChart() {
    double minX = _memSpots.isEmpty ? 0 : _memSpots.first.x;
    double maxX = _memSpots.isEmpty ? 60 : _memSpots.last.x;
    
    // Auto-scale Y axis based on limit or current usage
    final limit = (_latestData?['memory_limit_mb'] as num?)?.toDouble() ?? 512.0;
    double maxY = limit;
    if (_memSpots.isNotEmpty) {
      final maxVal = _memSpots.map((e) => e.y).reduce((a, b) => a > b ? a : b);
      if (maxVal > maxY) maxY = maxVal + 100;
    }
    
    return GlassCard(
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Memory Usage', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
            const SizedBox(height: 16),
            SizedBox(
              height: 200,
              child: LineChart(
                LineChartData(
                  minX: minX,
                  maxX: maxX,
                  minY: 0,
                  maxY: maxY,
                  titlesData: FlTitlesData(
                    show: true,
                    topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                    rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                    bottomTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                    leftTitles: AxisTitles(
                      sideTitles: SideTitles(
                        showTitles: true,
                        reservedSize: 40,
                        getTitlesWidget: (v, meta) => Text('${v.toInt()}MB', style: const TextStyle(color: Colors.white30, fontSize: 10)),
                      ),
                    ),
                  ),
                  gridData: FlGridData(
                    show: true,
                    drawVerticalLine: false,
                    getDrawingHorizontalLine: (value) => FlLine(color: Colors.white.withOpacity(0.05), strokeWidth: 1),
                  ),
                  borderData: FlBorderData(show: false),
                  lineBarsData: [
                    LineChartBarData(
                      spots: _memSpots,
                      isCurved: true,
                      color: Colors.purpleAccent,
                      barWidth: 3,
                      isStrokeCapRound: true,
                      dotData: const FlDotData(show: false),
                      belowBarData: BarAreaData(
                        show: true,
                        gradient: LinearGradient(
                          colors: [
                            Colors.purpleAccent.withOpacity(0.3),
                            Colors.purpleAccent.withOpacity(0.0),
                          ],
                          begin: Alignment.topCenter,
                          end: Alignment.bottomCenter,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
