import 'package:flutter/material.dart';
import '../../api/erex_api.dart';
import '../../models/database_models.dart';
import '../../state/app_state.dart';
import '../widgets/glass_card.dart';
import 'create_database_screen.dart';

class DatabasesScreen extends StatefulWidget {
  const DatabasesScreen({super.key, required this.state});
  final AppState state;

  @override
  State<DatabasesScreen> createState() => _DatabasesScreenState();
}

class _DatabasesScreenState extends State<DatabasesScreen> {
  List<DatabaseInstance> _databases = [];
  bool _isLoading = true;
  String? _mutatingId;

  @override
  void initState() {
    super.initState();
    _loadDatabases();
  }

  Future<void> _mutateDatabase(
    DatabaseInstance db,
    Future<void> Function() action,
  ) async {
    setState(() => _mutatingId = db.id);
    try {
      await action();
      await _loadDatabases();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Database action failed: $e')));
      }
    } finally {
      if (mounted) setState(() => _mutatingId = null);
    }
  }

  Future<void> _confirmDelete(DatabaseInstance db) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF161622),
        title: const Text('Delete database?', style: TextStyle(color: Colors.white)),
        content: Text(
          'This will delete ${db.name} and its active volume on the node. Existing object-storage backups are retained.',
          style: const TextStyle(color: Colors.white70),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Delete', style: TextStyle(color: Colors.redAccent)),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await _mutateDatabase(db, () => widget.state.api.deleteDatabase(db.id));
    }
  }

  Future<void> _loadDatabases() async {
    setState(() => _isLoading = true);
    try {
      final databases = await widget.state.api.getDatabases();
      if (mounted) setState(() => _databases = databases);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Error loading databases: $e')));
      }
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent, // Assumes parent has the background
      appBar: AppBar(
        title: const Text('Databases'),
        backgroundColor: Colors.transparent,
        elevation: 0,
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _loadDatabases,
          )
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () async {
          final result = await Navigator.push(
            context,
            MaterialPageRoute(builder: (context) => CreateDatabaseScreen(state: widget.state)),
          );
          if (result == true) {
            _loadDatabases();
          }
        },
        backgroundColor: const Color(0xFF06B6D4),
        icon: const Icon(Icons.add, color: Colors.black),
        label: const Text('Create Database', style: TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator(color: Color(0xFF06B6D4)))
          : _databases.isEmpty
              ? const Center(child: Text('No databases found. Create one!', style: TextStyle(color: Colors.white70)))
              : ListView.builder(
                  padding: const EdgeInsets.all(16),
                  itemCount: _databases.length,
                  itemBuilder: (context, index) {
                    final db = _databases[index];
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 16),
                      child: GlassCard(
                        child: Padding(
                          padding: const EdgeInsets.all(16.0),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Row(
                                children: [
                                  Container(
                                    width: 48,
                                    height: 48,
                                    decoration: BoxDecoration(
                                      color: const Color(0xFF06B6D4).withOpacity(0.1),
                                      borderRadius: BorderRadius.circular(12),
                                    ),
                                    child: Icon(
                                      db.engine == 'postgresql' ? Icons.storage : Icons.memory,
                                      color: const Color(0xFF06B6D4),
                                    ),
                                  ),
                                  const SizedBox(width: 16),
                                  Expanded(
                                    child: Column(
                                      crossAxisAlignment: CrossAxisAlignment.start,
                                      children: [
                                        Text(db.name, style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
                                        const SizedBox(height: 4),
                                        Text('${db.engine} ${db.version}', style: const TextStyle(color: Colors.white70, fontSize: 12)),
                                      ],
                                    ),
                                  ),
                                  Container(
                                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                                    decoration: BoxDecoration(
                                      color: db.state == 'running' ? Colors.greenAccent.withOpacity(0.2) : Colors.amber.withOpacity(0.2),
                                      borderRadius: BorderRadius.circular(4),
                                    ),
                                    child: Text(db.state, style: TextStyle(color: db.state == 'running' ? Colors.greenAccent : Colors.amber, fontSize: 10)),
                                  )
                                ],
                              ),
                              const SizedBox(height: 16),
                              Text('Internal Host: ${db.internalHostname}:${db.internalPort}', style: const TextStyle(color: Colors.white54, fontSize: 12)),
                              Text('Database Name: ${db.dbName}', style: const TextStyle(color: Colors.white54, fontSize: 12)),
                              Text('Username: ${db.dbUsername}', style: const TextStyle(color: Colors.white54, fontSize: 12)),
                              const SizedBox(height: 16),
                              Row(
                                children: [
                                  Expanded(
                                    child: OutlinedButton(
                                      onPressed: _mutatingId == db.id
                                          ? null
                                          : () => _mutateDatabase(db, () async {
                                                await widget.state.api.startDatabase(db.id);
                                              }),
                                      child: const Text('Start'),
                                    ),
                                  ),
                                  const SizedBox(width: 8),
                                  Expanded(
                                    child: OutlinedButton(
                                      onPressed: _mutatingId == db.id
                                          ? null
                                          : () => _mutateDatabase(db, () async {
                                                await widget.state.api.stopDatabase(db.id);
                                              }),
                                      child: const Text('Stop'),
                                    ),
                                  ),
                                  const SizedBox(width: 8),
                                  IconButton(
                                    tooltip: 'Delete',
                                    icon: const Icon(Icons.delete_outline, color: Colors.redAccent),
                                    onPressed: _mutatingId == db.id ? null : () => _confirmDelete(db),
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ),
                      ),
                    );
                  },
                ),
    );
  }
}
