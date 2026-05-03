from __future__ import annotations

from typing import Any


class EngineExecute:
    def __init__(self, runner):
        self._runner = runner

    def execute(self):
        return self._runner()


class EngineValuesFacade:
    def __init__(self, engine, sheetKey: str):
        self._engine = engine
        self._sheetKey = sheetKey

    def get(self, spreadsheetId: str, range: str, **kwargs):
        return EngineExecute(
            lambda: {"values": self._engine.getValues(self._sheetKey, range, **kwargs)}
        )

    def batchGet(self, spreadsheetId: str, ranges: list[str], **kwargs):
        return EngineExecute(
            lambda: {"valueRanges": self._engine.batchGetValues(self._sheetKey, ranges, **kwargs)}
        )

    def batchUpdate(self, spreadsheetId: str, body: dict):
        data = body.get("data", []) if isinstance(body, dict) else []
        return EngineExecute(lambda: self._engine.batchUpdateValues(self._sheetKey, data) or {})

    def append(
        self,
        spreadsheetId: str,
        range: str,
        valueInputOption: str,
        insertDataOption: str,
        body: dict,
    ):
        values = body.get("values", []) if isinstance(body, dict) else []
        return EngineExecute(
            lambda: self._engine.appendValues(
                self._sheetKey,
                rangeA1=range,
                values=values,
                valueInputOption=valueInputOption,
                insertDataOption=insertDataOption,
            )
        )


class EngineSpreadsheetsFacade:
    def __init__(self, engine, sheetKey: str):
        self._engine = engine
        self._sheetKey = sheetKey

    def values(self):
        return EngineValuesFacade(self._engine, self._sheetKey)

    def batchUpdate(self, spreadsheetId: str, body: dict):
        requests = body.get("requests", []) if isinstance(body, dict) else []
        return EngineExecute(lambda: self._engine.batchUpdateRequests(self._sheetKey, requests) or {})

    def get(self, spreadsheetId: str):
        return EngineExecute(lambda: self._engine.getSpreadsheetMetadata(self._sheetKey))


class EngineServiceFacade:
    def __init__(self, engine, sheetKey: str):
        self._engine = engine
        self._sheetKey = sheetKey

    def spreadsheets(self):
        return EngineSpreadsheetsFacade(self._engine, self._sheetKey)


def createEngineServiceFacade(engine: Any, sheetKey: str) -> EngineServiceFacade:
    return EngineServiceFacade(engine, sheetKey)
