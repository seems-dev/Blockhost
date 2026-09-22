import 'dart:ui';
import 'package:flutter/material.dart';
import '../theme/tranquil_theme.dart';
import '../../services/audio_service.dart';

class GlassCard extends StatefulWidget {
  const GlassCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(16),
    this.margin = EdgeInsets.zero,
    this.isSelected = false,
    this.onTap,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final EdgeInsetsGeometry margin;
  final bool isSelected;
  final VoidCallback? onTap;

  @override
  State<GlassCard> createState() => _GlassCardState();
}

class _GlassCardState extends State<GlassCard> {
  bool _isHovering = false;

  @override
  Widget build(BuildContext context) {
    Widget card = ClipRRect(
      borderRadius: BorderRadius.circular(12),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: TranquilTheme.blurSigma, sigmaY: TranquilTheme.blurSigma),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOutCubic,
          padding: widget.padding,
          decoration: TranquilTheme.glassDecoration(
            isSelected: widget.isSelected || _isHovering,
          ),
          child: Stack(
            clipBehavior: Clip.none,
            children: [
              widget.child,
              if (widget.isSelected) ...[
                Positioned(top: -4, left: -4, child: _buildCornerDot()),
                Positioned(top: -4, right: -4, child: _buildCornerDot()),
                Positioned(bottom: -4, left: -4, child: _buildCornerDot()),
                Positioned(bottom: -4, right: -4, child: _buildCornerDot()),
              ]
            ],
          ),
        ),
      ),
    );

    if (widget.onTap != null) {
      card = GestureDetector(
        onTap: () {
          AudioService.playClick();
          widget.onTap!();
        },
        behavior: HitTestBehavior.opaque,
        child: MouseRegion(
          onEnter: (_) => setState(() => _isHovering = true),
          onExit: (_) => setState(() => _isHovering = false),
          cursor: SystemMouseCursors.click,
          child: AnimatedScale(
            scale: _isHovering ? 1.01 : 1.0,
            duration: const Duration(milliseconds: 150),
            curve: Curves.easeOutCubic,
            child: card,
          ),
        ),
      );
    } else {
      card = MouseRegion(
        onEnter: (_) => setState(() => _isHovering = true),
        onExit: (_) => setState(() => _isHovering = false),
        child: card,
      );
    }

    if (widget.margin != EdgeInsets.zero) {
      card = Padding(padding: widget.margin, child: card);
    }

    return card;
  }

  Widget _buildCornerDot() {
    return Container(
      width: 4,
      height: 4,
      decoration: BoxDecoration(
        color: TranquilTheme.glowCyan,
        shape: BoxShape.circle,
        boxShadow: const [
          BoxShadow(color: TranquilTheme.glowCyan, blurRadius: 4, spreadRadius: 1)
        ],
      ),
    );
  }
}
