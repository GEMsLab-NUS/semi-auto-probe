"""Display-only I-V history; raw measurement storage is independent."""

def spread_indices(count, limit):
    if count <= limit:
        return list(range(count))
    return [round(i * (count - 1) / (limit - 1)) for i in range(limit)]


class IVPlotBuffer(list):
    """Keep references grouped by four feature-point legs, including turning endpoints."""
    def __init__(self):
        super().__init__()
        self.curves = []
        self._cycle = None

    def append(self, sample):
        cycle = sample.sweep_leg // 4
        if cycle != self._cycle:
            self.curves.append([self[-1]] if self else [])
            self._cycle = cycle
        self.curves[-1].append(sample)
        super().append(sample)

    def clear(self):
        super().clear()
        self.curves.clear()
        self._cycle = None

    def display_curves(self, limit=100, points_per_curve=2000):
        return [[self.curves[i][j] for j in spread_indices(len(self.curves[i]), points_per_curve)]
                for i in spread_indices(len(self.curves), limit)]
