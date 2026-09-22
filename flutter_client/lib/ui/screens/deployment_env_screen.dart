import 'package:flutter/material.dart';
import '../../state/app_state.dart';
import '../widgets/glass_card.dart';

class DeploymentEnvScreen extends StatefulWidget {
  const DeploymentEnvScreen({super.key, required this.state, required this.deploymentId});
  
  final AppState state;
  final String deploymentId;

  @override
  State<DeploymentEnvScreen> createState() => _DeploymentEnvScreenState();
}

class _DeploymentEnvScreenState extends State<DeploymentEnvScreen> {
  bool _isLoading = true;
  bool _isSaving = false;
  Map<String, String> _envVars = {};
  
  final _keyCtrl = TextEditingController();
  final _valCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    _loadEnvVars();
  }

  Future<void> _loadEnvVars() async {
    setState(() => _isLoading = true);
    try {
      final env = await widget.state.api.getEnvVars(widget.deploymentId, decrypt: true);
      if (mounted) {
        setState(() {
          _envVars = env;
        });
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Error loading env vars: $e')));
      }
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _saveEnvVars() async {
    setState(() => _isSaving = true);
    try {
      await widget.state.api.updateEnvVars(widget.deploymentId, _envVars);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Environment variables saved successfully. Deploying...'),
            backgroundColor: Colors.green,
          )
        );
        Navigator.of(context).pop(true);
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed to save: $e')));
      }
    } finally {
      if (mounted) setState(() => _isSaving = false);
    }
  }

  void _addVar() {
    final k = _keyCtrl.text.trim();
    final v = _valCtrl.text.trim();
    if (k.isEmpty || v.isEmpty) return;
    
    setState(() {
      _envVars[k] = v;
      _keyCtrl.clear();
      _valCtrl.clear();
    });
  }

  void _removeVar(String key) {
    setState(() {
      _envVars.remove(key);
    });
  }

  @override
  void dispose() {
    _keyCtrl.dispose();
    _valCtrl.dispose();
    super.dispose();
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
                    const Text('Environment Variables', style: TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.bold)),
                  ],
                ),
              ),
              
              if (_isLoading)
                const Expanded(child: Center(child: CircularProgressIndicator(color: Color(0xFF06B6D4))))
              else
                Expanded(
                  child: ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      GlassCard(
                        child: Padding(
                          padding: const EdgeInsets.all(16),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              const Text('Add Variable', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
                              const SizedBox(height: 16),
                              Row(
                                children: [
                                  Expanded(
                                    flex: 2,
                                    child: TextField(
                                      controller: _keyCtrl,
                                      style: const TextStyle(color: Colors.white),
                                      decoration: InputDecoration(
                                        labelText: 'KEY (e.g. DATABASE_URL)',
                                        labelStyle: const TextStyle(color: Colors.white54, fontSize: 12),
                                        filled: true,
                                        fillColor: Colors.black.withOpacity(0.3),
                                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide.none),
                                        contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                                      ),
                                    ),
                                  ),
                                  const SizedBox(width: 8),
                                  Expanded(
                                    flex: 3,
                                    child: TextField(
                                      controller: _valCtrl,
                                      style: const TextStyle(color: Colors.white),
                                      decoration: InputDecoration(
                                        labelText: 'VALUE',
                                        labelStyle: const TextStyle(color: Colors.white54, fontSize: 12),
                                        filled: true,
                                        fillColor: Colors.black.withOpacity(0.3),
                                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide.none),
                                        contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                                      ),
                                    ),
                                  ),
                                  const SizedBox(width: 8),
                                  Container(
                                    decoration: BoxDecoration(
                                      color: const Color(0xFF06B6D4).withOpacity(0.2),
                                      borderRadius: BorderRadius.circular(8),
                                    ),
                                    child: IconButton(
                                      icon: const Icon(Icons.add, color: Color(0xFF06B6D4)),
                                      onPressed: _addVar,
                                    ),
                                  )
                                ],
                              )
                            ],
                          ),
                        )
                      ),
                      
                      const SizedBox(height: 24),
                      
                      if (_envVars.isEmpty)
                        const Center(child: Padding(
                          padding: EdgeInsets.all(32.0),
                          child: Text('No environment variables set.', style: TextStyle(color: Colors.white54)),
                        ))
                      else
                        ..._envVars.entries.map((e) {
                          return Container(
                            margin: const EdgeInsets.only(bottom: 8),
                            decoration: BoxDecoration(
                              color: Colors.black.withOpacity(0.2),
                              borderRadius: BorderRadius.circular(8),
                              border: Border.all(color: Colors.white.withOpacity(0.05)),
                            ),
                            child: ListTile(
                              title: Text(e.key, style: const TextStyle(color: Color(0xFF06B6D4), fontWeight: FontWeight.bold, fontSize: 14)),
                              subtitle: Text(e.value, style: const TextStyle(color: Colors.white70, fontSize: 12)),
                              trailing: IconButton(
                                icon: const Icon(Icons.delete_outline, color: Colors.redAccent, size: 20),
                                onPressed: () => _removeVar(e.key),
                              ),
                            ),
                          );
                        }),
                    ],
                  ),
                ),
                
              if (!_isLoading)
                Padding(
                  padding: const EdgeInsets.all(16.0),
                  child: ElevatedButton(
                    onPressed: _isSaving ? null : _saveEnvVars,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: const Color(0xFF06B6D4),
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(vertical: 16),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                    ),
                    child: _isSaving 
                      ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2))
                      : const Text('Save & Apply Changes', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                  ),
                )
            ],
          ),
        ),
      ),
    );
  }
}
