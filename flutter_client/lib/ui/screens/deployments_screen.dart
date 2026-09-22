import 'package:flutter/material.dart';
import '../../api/erex_api.dart';
import '../../models/deployment_models.dart';
import '../../state/app_state.dart';
import '../theme/tranquil_theme.dart';
import '../widgets/glass_card.dart';
import 'create_deployment_screen.dart';
import 'deployment_detail_screen.dart';

class DeploymentsScreen extends StatefulWidget {
  const DeploymentsScreen({super.key, required this.state});
  final AppState state;

  @override
  State<DeploymentsScreen> createState() => _DeploymentsScreenState();
}

class _DeploymentsScreenState extends State<DeploymentsScreen> {
  late Future<List<AppDeployment>> _future;

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    setState(() {
      _future = widget.state.api.getDeployments();
    });
  }

  void _goToCreate() async {
    final result = await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => CreateDeploymentScreen(state: widget.state),
      ),
    );
    if (result == true) {
      _load();
    }
  }

  void _goToDetail(AppDeployment dep) async {
    final result = await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => DeploymentDetailScreen(state: widget.state, deploymentId: dep.id),
      ),
    );
    // Reload in case they changed state
    _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(
        title: const Text('PaaS Apps'),
        backgroundColor: Colors.transparent,
        elevation: 0,
        actions: [
          IconButton(
            icon: const Icon(Icons.add, color: Color(0xFF06B6D4)),
            onPressed: _goToCreate,
          )
        ],
      ),
      body: FutureBuilder<List<AppDeployment>>(
        future: _future,
        builder: (context, snapshot) {
          if (snapshot.connectionState == ConnectionState.waiting) {
            return const Center(
              child: CircularProgressIndicator(color: Color(0xFF06B6D4)),
            );
          }
          if (snapshot.hasError) {
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(16.0),
                child: Text('Error: ${snapshot.error}', style: const TextStyle(color: Colors.red)),
              ),
            );
          }

          final items = snapshot.data ?? [];
          if (items.isEmpty) {
            return Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const Icon(Icons.cloud_off, size: 64, color: Colors.white54),
                  const SizedBox(height: 16),
                  const Text('No apps deployed', style: TextStyle(color: Colors.white, fontSize: 18)),
                  const SizedBox(height: 16),
                  ElevatedButton(
                    onPressed: _goToCreate,
                    style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF06B6D4)),
                    child: const Text('Deploy an App', style: TextStyle(color: Colors.black)),
                  )
                ],
              ),
            );
          }

          return RefreshIndicator(
            onRefresh: () async => _load(),
            color: const Color(0xFF06B6D4),
            child: ListView.builder(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 100),
              itemCount: items.length,
              itemBuilder: (context, index) {
                final dep = items[index];
                return Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: GestureDetector(
                    onTap: () => _goToDetail(dep),
                    child: GlassCard(
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Row(
                          children: [
                            Container(
                              width: 48,
                              height: 48,
                              decoration: BoxDecoration(
                                color: const Color(0xFF06B6D4).withOpacity(0.1),
                                borderRadius: BorderRadius.circular(12),
                              ),
                              child: const Icon(Icons.widgets_outlined, color: Color(0xFF06B6D4)),
                            ),
                            const SizedBox(width: 16),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    dep.name,
                                    style: const TextStyle(
                                      color: Colors.white,
                                      fontSize: 16,
                                      fontWeight: FontWeight.bold,
                                    ),
                                  ),
                                  const SizedBox(height: 4),
                                  Text(
                                    dep.dockerImage,
                                    style: const TextStyle(
                                      color: Colors.white70,
                                      fontSize: 13,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            _StatusBadge(state: dep.state),
                          ],
                        ),
                      ),
                    ),
                  ),
                );
              },
            ),
          );
        },
      ),
    );
  }
}

class _StatusBadge extends StatelessWidget {
  const _StatusBadge({required this.state});
  final String state;

  @override
  Widget build(BuildContext context) {
    Color color;
    switch (state) {
      case 'running':
        color = Colors.greenAccent;
        break;
      case 'suspended':
        color = Colors.amber;
        break;
      case 'building':
      case 'created':
        color = Colors.blueAccent;
        break;
      case 'error':
        color = Colors.redAccent;
        break;
      default:
        color = Colors.grey;
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: color.withOpacity(0.15),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withOpacity(0.5)),
      ),
      child: Text(
        state.toUpperCase(),
        style: TextStyle(
          color: color,
          fontSize: 10,
          fontWeight: FontWeight.bold,
        ),
      ),
    );
  }
}
