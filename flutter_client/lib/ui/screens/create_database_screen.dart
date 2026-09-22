import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../api/erex_api.dart';
import '../../models/database_models.dart';
import '../../state/app_state.dart';
import '../widgets/glass_card.dart';

class CreateDatabaseScreen extends StatefulWidget {
  const CreateDatabaseScreen({super.key, required this.state});
  final AppState state;

  @override
  State<CreateDatabaseScreen> createState() => _CreateDatabaseScreenState();
}

class _CreateDatabaseScreenState extends State<CreateDatabaseScreen> {
  final _nameCtrl = TextEditingController();
  String _selectedEngine = 'postgresql';
  int _selectedRamMb = 512;
  bool _isLoading = false;

  void _submit() async {
    final name = _nameCtrl.text.trim();
    if (name.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Please enter a name')));
      return;
    }

    setState(() => _isLoading = true);
    try {
      final project = await widget.state.api.getOrCreateDefaultProject();

      final dbInstance = await widget.state.api.createDatabase(
        name: name,
        engine: _selectedEngine,
        version: _selectedEngine == 'postgresql' ? '16' : '8.0',
        ramLimitMb: _selectedRamMb,
        projectId: project.id,
      );

      if (mounted) {
        // Show success modal with credentials!
        await showDialog(
          context: context,
          barrierDismissible: false,
          builder: (ctx) => AlertDialog(
            backgroundColor: const Color(0xFF161622),
            title: const Text('Database Created!', style: TextStyle(color: Colors.white)),
            content: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Save this connection string now. You will NEVER be able to see this password again!', style: TextStyle(color: Colors.redAccent, fontWeight: FontWeight.bold)),
                const SizedBox(height: 16),
                const Text('Connection URL:', style: TextStyle(color: Colors.white70, fontSize: 12)),
                const SizedBox(height: 8),
                SelectableText(dbInstance.connectionUrl ?? 'N/A', style: const TextStyle(color: Colors.white, fontFamily: 'monospace', fontSize: 13)),
              ],
            ),
            actions: [
              TextButton(
                onPressed: () {
                  if (dbInstance.connectionUrl != null) {
                    Clipboard.setData(ClipboardData(text: dbInstance.connectionUrl!));
                    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Copied to clipboard!')));
                  }
                },
                child: const Text('Copy to Clipboard', style: TextStyle(color: Color(0xFF06B6D4))),
              ),
              TextButton(
                onPressed: () {
                  Navigator.pop(ctx);
                  Navigator.pop(context, true);
                },
                child: const Text('I saved it', style: TextStyle(color: Colors.white)),
              ),
            ],
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Error: $e'), backgroundColor: Colors.red));
      }
    } finally {
      if (mounted) {
        setState(() => _isLoading = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent, // Assumes parent has the background
      appBar: AppBar(
        title: const Text('Create Database'),
        backgroundColor: Colors.transparent,
        elevation: 0,
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator(color: Color(0xFF06B6D4)))
          : SingleChildScrollView(
              padding: const EdgeInsets.all(16.0),
              child: Column(
                children: [
                  GlassCard(
                    child: Padding(
                      padding: const EdgeInsets.all(16.0),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('Configuration', style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.bold)),
                          const SizedBox(height: 16),
                          _buildTextField('Database Name', _nameCtrl, 'e.g., My Production DB'),
                          const SizedBox(height: 16),
                          const Text('Engine', style: TextStyle(color: Colors.white70, fontSize: 12)),
                          const SizedBox(height: 8),
                          DropdownButtonFormField<String>(
                            value: _selectedEngine,
                            dropdownColor: const Color(0xFF161622),
                            decoration: _inputDeco(),
                            items: const [
                              DropdownMenuItem(value: 'postgresql', child: Text('PostgreSQL 16', style: TextStyle(color: Colors.white))),
                              DropdownMenuItem(value: 'redis', child: Text('Redis 7', style: TextStyle(color: Colors.white))),
                            ],
                            onChanged: (v) {
                              if (v != null) setState(() => _selectedEngine = v);
                            },
                          ),
                          const SizedBox(height: 16),
                          const Text('RAM Limit', style: TextStyle(color: Colors.white70, fontSize: 12)),
                          const SizedBox(height: 8),
                          DropdownButtonFormField<int>(
                            value: _selectedRamMb,
                            dropdownColor: const Color(0xFF161622),
                            decoration: _inputDeco(),
                            items: [256, 512, 1024, 2048, 4096].map((mb) {
                              return DropdownMenuItem(value: mb, child: Text('${mb}MB', style: TextStyle(color: Colors.white)));
                            }).toList(),
                            onChanged: (v) {
                              if (v != null) setState(() => _selectedRamMb = v);
                            },
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 24),
                  SizedBox(
                    width: double.infinity,
                    height: 56,
                    child: ElevatedButton(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFF06B6D4),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                      ),
                      onPressed: _submit,
                      child: const Text(
                        'Create Database',
                        style: TextStyle(color: Colors.black, fontSize: 16, fontWeight: FontWeight.bold),
                      ),
                    ),
                  )
                ],
              ),
            ),
    );
  }

  Widget _buildTextField(String label, TextEditingController controller, String hint) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: const TextStyle(color: Colors.white70, fontSize: 12)),
        const SizedBox(height: 8),
        TextField(
          controller: controller,
          style: const TextStyle(color: Colors.white),
          decoration: _inputDeco().copyWith(hintText: hint),
        ),
      ],
    );
  }

  InputDecoration _inputDeco() {
    return InputDecoration(
      filled: true,
      fillColor: Colors.black.withOpacity(0.2),
      hintStyle: const TextStyle(color: Colors.white30),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: Colors.white.withOpacity(0.1))),
      enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: Colors.white.withOpacity(0.1))),
      focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: const BorderSide(color: Color(0xFF06B6D4))),
    );
  }
}
