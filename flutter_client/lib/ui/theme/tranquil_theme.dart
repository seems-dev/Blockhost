import 'package:flutter/material.dart';

class TranquilTheme {
  // Base Colors
  static const Color glowCyan = Color(0xFF64FFDA);
  static const Color darkTeal = Color(0xFF003333);
  static const Color deepWater = Color(0xFF051923);
  static const Color paleCyan = Color(0xFFE0F7FA);

  // Text Colors
  static const Color textPrimary = Color(0xFF0F172A); // Dark zinc
  static const Color textSecondary = Color(0xFF475569); // Zinc secondary
  static const Color textMuted = Color(0xFF64748B); // Muted zinc
  static const Color textBright = textPrimary;

  // Opacities
  static const double glassOpacity = 0.25;
  static const double blurSigma = 10.0;

  // Decorators
  static BoxDecoration glassDecoration({bool isSelected = false}) {
    return BoxDecoration(
      color: deepWater.withOpacity(glassOpacity),
      borderRadius: BorderRadius.circular(12),
      border: Border.all(
        color:
            isSelected ? glowCyan.withOpacity(0.8) : glowCyan.withOpacity(0.2),
        width: isSelected ? 1.5 : 1.0,
      ),
      boxShadow: isSelected
          ? [
              BoxShadow(
                  color: glowCyan.withOpacity(0.2),
                  blurRadius: 12,
                  spreadRadius: 2)
            ]
          : [],
    );
  }

  // Gradients
  static const LinearGradient buttonGradient = LinearGradient(
    colors: [Color(0xFF00B4DB), Color(0xFF0083B0)],
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );

  static const LinearGradient buttonHoverGradient = LinearGradient(
    colors: [Color(0xFF00E5FF), Color(0xFF00B4DB)],
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );
}
