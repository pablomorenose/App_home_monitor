"""Utilidades compartidas entre la app web y el worker de monitorización."""


def humanize_duration(seconds: float) -> str:
    """Formatea una duración en segundos como '2d 3h', '15min' o '42s'."""
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:
        parts.append(f"{minutes}min")
    if not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)
