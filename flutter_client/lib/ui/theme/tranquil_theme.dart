import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

class TranquilTheme {
  // Base Colors
  static const Color glowCyan = Color(0xFF64FFDA);
  static const Color glowIndigo = Color(0xFF6366F1);
  static const Color darkTeal = Color(0xFF003333);
  static const Color deepWater = Color(0xFF090D16);
  static const Color paleCyan = Color(0xFFE0F7FA);
  
  // Danger/Warning Colors
  static const Color errorRed = Color(0xFFF43F5E);
  static const Color warningOrange = Color(0xFFF59E0B);
  static const Color successGreen = Color(0xFF10B981);

  // Text Colors
  static const Color textPrimary = Color(0xFFF8FAFC); // Light slate
  static const Color textSecondary = Color(0xFF94A3B8); // Slate 400
  static const Color textMuted = Color(0xFF475569); // Slate 600
  static const Color textBright = Colors.white;

  // Opacities
  static const double glassOpacity = 0.4;
  static const double blurSigma = 12.0;

  // Typography
  static TextTheme textTheme(TextTheme base) {
    return GoogleFonts.interTextTheme(base).copyWith(
      displayLarge: GoogleFonts.inter(color: textPrimary, fontWeight: FontWeight.bold),
      displayMedium: GoogleFonts.inter(color: textPrimary, fontWeight: FontWeight.bold),
      displaySmall: GoogleFonts.inter(color: textPrimary, fontWeight: FontWeight.w600),
      headlineMedium: GoogleFonts.inter(color: textPrimary, fontWeight: FontWeight.w600),
      titleLarge: GoogleFonts.inter(color: textPrimary, fontWeight: FontWeight.w600),
      titleMedium: GoogleFonts.inter(color: textSecondary, fontWeight: FontWeight.w500),
      bodyLarge: GoogleFonts.inter(color: textPrimary),
      bodyMedium: GoogleFonts.inter(color: textSecondary),
      bodySmall: GoogleFonts.inter(color: textMuted),
      labelLarge: GoogleFonts.inter(color: textPrimary, fontWeight: FontWeight.bold),
    );
  }
  
  // Theme Data
  static ThemeData get themeData {
    final base = ThemeData.dark();
    return base.copyWith(
      scaffoldBackgroundColor: deepWater,
      primaryColor: glowCyan,
      colorScheme: ColorScheme.dark(
        primary: glowCyan,
        secondary: glowIndigo,
        surface: deepWater,
        background: deepWater,
        error: errorRed,
      ),
      textTheme: textTheme(base.textTheme),
      appBarTheme: AppBarTheme(
        backgroundColor: Colors.transparent,
        elevation: 0,
        centerTitle: true,
        titleTextStyle: GoogleFonts.inter(
          color: textPrimary,
          fontSize: 20,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }

  // Decorators
  static BoxDecoration glassDecoration({bool isSelected = false, double borderRadius = 12.0}) {
    return BoxDecoration(
      color: Colors.white.withOpacity(0.03),
      borderRadius: BorderRadius.circular(borderRadius),
      border: Border.all(
        color: isSelected ? glowCyan.withOpacity(0.8) : Colors.white.withOpacity(0.1),
        width: isSelected ? 1.5 : 1.0,
      ),
      boxShadow: isSelected
          ? [
              BoxShadow(
                color: glowCyan.withOpacity(0.15),
                blurRadius: 16,
                spreadRadius: 2,
              )
            ]
          : [
              BoxShadow(
                color: Colors.black.withOpacity(0.2),
                blurRadius: 10,
                offset: const Offset(0, 4),
              )
            ],
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
  
  static const LinearGradient primaryGradient = LinearGradient(
    colors: [glowIndigo, Color(0xFF00E5FF)],
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );
}
