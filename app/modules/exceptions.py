# ============================================================================
# app/modules/exceptions.py - Exceptions personnalisées
# ============================================================================

class ZADAException(Exception):
    """Exception de base pour les erreurs ZADA"""
    pass

class FileLoadingError(ZADAException):
    """Erreur lors du chargement de fichiers"""
    pass

class GeometryProcessingError(ZADAException):
    """Erreur lors du traitement géométrique"""
    pass

class FusionError(ZADAException):
    """Erreur lors de la fusion"""
    pass