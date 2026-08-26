import 'package:flutter/material.dart';
import '../theme/tranquil_theme.dart';
import '../../services/audio_service.dart';

class GlowingButton extends StatefulWidget {
  const GlowingButton({
    super.key,
    required this.label,
    required this.onTap,
    this.icon,
    this.busy = false,
  });

  final String label;
  final VoidCallback? onTap;
  final IconData? icon;
  final bool busy;

  @override
  State<GlowingButton> createState() => _GlowingButtonState();
}

class _GlowingButtonState extends State<GlowingButton> {
  bool _isHovering = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      onEnter: (_) => setState(() => _isHovering = true),
      onExit: (_) => setState(() => _isHovering = false),
      child: GestureDetector(
        onTap: widget.busy ? null : () {
          if (widget.onTap != null) {
            AudioService.playClick();
            widget.onTap!();
          }
        },
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          height: 54,
          decoration: BoxDecoration(
            gradient: _isHovering ? TranquilTheme.buttonHoverGradient : TranquilTheme.buttonGradient,
            borderRadius: BorderRadius.circular(8),
            boxShadow: _isHovering ? [
              BoxShadow(color: TranquilTheme.glowCyan.withOpacity(0.4), blurRadius: 16, spreadRadius: 2)
            ] : [
              BoxShadow(color: TranquilTheme.glowCyan.withOpacity(0.15), blurRadius: 8, spreadRadius: 0)
            ],
          ),
          alignment: Alignment.center,
          child: widget.busy
              ? const SizedBox(
                  width: 20, height: 20,
                  child: CircularProgressIndicator(color: TranquilTheme.textBright, strokeWidth: 2),
                )
              : Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    if (widget.icon != null) ...[
                      Icon(widget.icon, color: TranquilTheme.textBright, size: 18),
                      const SizedBox(width: 8),
                    ],
                    Text(
                      widget.label,
                      style: const TextStyle(
                        color: TranquilTheme.textBright, 
                        fontSize: 15, 
                        fontWeight: FontWeight.w800, 
                        fontFamily: 'monospace'
                      ),
                    ),
                  ],
                ),
        ),
      ),
    );
  }
}
