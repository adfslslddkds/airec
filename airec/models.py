from dataclasses import asdict, dataclass
from enum import Enum
import math


class State(str, Enum):
    STOPPED = "Готов"
    STARTING = "Запуск наблюдения…"
    WAITING = "Ожидание звонка"
    RECORDING = "Идёт запись"
    STOPPING = "Остановка…"
    ERROR = "Ошибка"


@dataclass(frozen=True)
class Config:
    schema_version: int = 1
    autostart: bool = False
    interval: float = 1.0

    def validate(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Неизвестная версия настроек")
        if type(self.autostart) is not bool:
            raise ValueError("Некорректный флажок запуска")
        if (type(self.interval) not in (int, float) or not math.isfinite(self.interval)
                or not 0.2 <= self.interval <= 10):
            raise ValueError("Интервал опроса: от 0.2 до 10 секунд")
        return self

    def to_dict(self):
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("Настройки должны быть объектом JSON")
        try:
            return cls(**data).validate()
        except TypeError as exc:
            raise ValueError("Неизвестные поля настроек") from exc


@dataclass(frozen=True)
class Event:
    state: State
    detail: str = ""
    path: str = ""
    refresh: bool = False
