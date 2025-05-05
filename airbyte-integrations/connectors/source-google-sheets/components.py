#
# Copyright (c) 2025 Airbyte, Inc., all rights reserved.
#

from __future__ import annotations

from dataclasses import dataclass
from pydantic.v1 import BaseModel, Extra
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Union
from requests.status_codes import codes as status_codes

import dpath
import logging
import re
import requests
import unidecode

from airbyte_cdk.sources.declarative.decoders.json_decoder import JsonDecoder
from airbyte_cdk.sources.declarative.extractors.dpath_extractor import DpathExtractor
from airbyte_cdk.sources.declarative.interpolation.interpolated_string import InterpolatedString
from airbyte_cdk.sources.declarative.partition_routers.single_partition_router import SinglePartitionRouter
from airbyte_cdk.sources.types import Config, StreamSlice


logger = logging.getLogger("airbyte")
TOKEN_PATTERN = re.compile(r"[A-Z]+[a-z]*|[a-z]+|\d+|(?P<NoToken>[^a-zA-Z\d]+)")
DEFAULT_SEPARATOR = "_"

class RawSchemaParser:
    config: Config

    def _extract_data(
        self,
        body: Mapping[str, Any],
        extraction_path: Optional[List[Union[InterpolatedString, str]]] = None,
        default: Any = None,
    ) -> Any:
        """
        Extracts data from the body based on the provided extraction path.
        """

        if not extraction_path:
            return body

        path = [node.eval(self.config) if not isinstance(node, str) else node for node in extraction_path]

        return dpath.get(body, path, default=default)  # type: ignore # extracted

    def _set_data(
        self, value: Any, body: MutableMapping[str, Any], extraction_path: Optional[List[Union[InterpolatedString, str]]] = None
    ) -> Any:
        """
        Sets data in the body based on the provided extraction path.
        """
        if not extraction_path:
            body = value

        path = [node.eval(self.config) if not isinstance(node, str) else node for node in extraction_path]

        dpath.set(body, path, value=value)

    def parse_raw_schema_values(
        self,
        raw_schema_data: MutableMapping[Any, Any],
        schema_pointer: List[Union[InterpolatedString, str]],
        key_pointer: List[Union[InterpolatedString, str]],
        names_conversion: bool,
    ):
        """
        1. Parses sheet headers from the provided raw schema. This method assumes that data is contiguous
            i.e: every cell contains a value and the first cell which does not contain a value denotes the end
            of the headers.
        2. Makes name conversion if required.
        3. Removes duplicated fields from the schema.
        Return a list of tuples with correct property index (by found in array), value and raw_schema
        """
        raw_schema_properties = self._extract_data(raw_schema_data, schema_pointer, default=[])
        duplicate_fields = set()
        parsed_schema_values = []
        seen_values = set()
        for property_index, raw_schema_property in enumerate(raw_schema_properties):
            raw_schema_property_value = self._extract_data(raw_schema_property, key_pointer)
            if not raw_schema_property_value:
                break
            if names_conversion:
                raw_schema_property_value = safe_name_conversion(raw_schema_property_value)

            if raw_schema_property_value in seen_values:
                duplicate_fields.add(raw_schema_property_value)
            seen_values.add(raw_schema_property_value)
            parsed_schema_values.append((property_index, raw_schema_property_value, raw_schema_property))

        if duplicate_fields:
            parsed_schema_values = [
                parsed_schema_value for parsed_schema_value in parsed_schema_values if parsed_schema_value[1] not in duplicate_fields
            ]

        return parsed_schema_values

    def parse(self, schema_type_identifier, records: Iterable[MutableMapping[Any, Any]]):
        """Removes duplicated fields and makes names conversion"""
        names_conversion = self.config.get("names_conversion", False)
        schema_pointer = schema_type_identifier.get("schema_pointer")
        key_pointer = schema_type_identifier["key_pointer"]
        parsed_properties = []
        for raw_schema_data in records:
            for _, parsed_value, raw_schema_property in self.parse_raw_schema_values(
                raw_schema_data, schema_pointer, key_pointer, names_conversion
            ):
                self._set_data(parsed_value, raw_schema_property, key_pointer)
                parsed_properties.append(raw_schema_property)
            self._set_data(parsed_properties, raw_schema_data, schema_pointer)
            yield raw_schema_data


@dataclass
class DpathSchemaMatchingExtractor(DpathExtractor, RawSchemaParser):
    """
    Current DpathExtractor has problems for this type of data in response:
    [
      {
        "values": [
          [
            "name1",
            "22"
          ],
          [
            "name2",
            "24"
          ],
          [
            "name3",
            "25"
          ]
        ]
      }
    ]

    This is because "values" field is a list of lists instead of objects that we could extract with "*".
    In order to do so we need the ordered properties from the schema that we can match with each list of values.
    Then, if we get a properties object like {0: 'name', 1: 'age'} we end up with:

    {"type":"RECORD","record":{"stream":"a_stream_name","data":{"name":"name1","age":"22"},"emitted_at":1734371904128}}
    {"type":"RECORD","record":{"stream":"a_stream_name","data":{"name":"name2","age":"24"},"emitted_at":1734371904134}}
    {"type":"RECORD","record":{"stream":"a_stream_name","data":{"name":"name3","age":"25"},"emitted_at":1734371904134}}
    """

    def __post_init__(self, parameters: Mapping[str, Any]) -> None:
        super().__post_init__(parameters)
        self.decoder = JsonDecoder(parameters={})
        self._values_to_match_key = parameters["values_to_match_key"]
        schema_type_identifier = parameters["schema_type_identifier"]
        names_conversion = self.config.get("names_conversion", False)
        self._indexed_properties_to_match = self.extract_properties_to_match(
            parameters["properties_to_match"], schema_type_identifier, names_conversion=names_conversion
        )

    def extract_properties_to_match(self, properties_to_match, schema_type_identifier, names_conversion):
        schema_pointer = schema_type_identifier.get("schema_pointer")
        key_pointer = schema_type_identifier["key_pointer"]
        indexed_properties = {}
        for property_index, property_parsed_value, _ in self.parse_raw_schema_values(
            properties_to_match, schema_pointer, key_pointer, names_conversion
        ):
            indexed_properties[property_index] = property_parsed_value
        return indexed_properties

    @staticmethod
    def match_properties_with_values(unmatched_values: List[str], indexed_properties: Dict[int, str]):
        data = {}
        for relevant_index in sorted(indexed_properties.keys()):
            if relevant_index >= len(unmatched_values):
                break

            unmatch_value = unmatched_values[relevant_index]
            if unmatch_value.strip() != "":
                data[indexed_properties[relevant_index]] = unmatch_value
        yield data

    @staticmethod
    def is_row_empty(cell_values: List[str]) -> bool:
        for cell in cell_values:
            if cell.strip() != "":
                return False
        return True

    @staticmethod
    def row_contains_relevant_data(cell_values: List[str], relevant_indices: Iterable[int]) -> bool:
        for idx in relevant_indices:
            if len(cell_values) > idx and cell_values[idx].strip() != "":
                return True
        return False

    def extract_records(self, response: requests.Response) -> Iterable[MutableMapping[Any, Any]]:
        raw_records_extracted = super().extract_records(response=response)
        for raw_record in raw_records_extracted:
            unmatched_values_collection = raw_record.get(self._values_to_match_key, [])
            for unmatched_values in unmatched_values_collection:
                if not DpathSchemaMatchingExtractor.is_row_empty(
                    unmatched_values
                ) and DpathSchemaMatchingExtractor.row_contains_relevant_data(unmatched_values, self._indexed_properties_to_match.keys()):
                    yield from DpathSchemaMatchingExtractor.match_properties_with_values(
                        unmatched_values, self._indexed_properties_to_match
                    )


class DpathSchemaExtractor(DpathExtractor, RawSchemaParser):
    """
    Makes names conversion and parses sheet headers from the provided row.
    """

    def __post_init__(self, parameters: Mapping[str, Any]) -> None:
        super().__post_init__(parameters)
        self.schema_type_identifier = parameters["schema_type_identifier"]

    def extract_records(self, response: requests.Response) -> Iterable[MutableMapping[Any, Any]]:
        extracted_records = super().extract_records(response=response)
        yield from self.parse(schema_type_identifier=self.schema_type_identifier, records=extracted_records)


class RangePartitionRouter(SinglePartitionRouter):
    """
    Create ranges to request rows data to google sheets api.
    """

    parameters: Mapping[str, Any]

    def __init__(self, parameters: Mapping[str, Any]) -> None:
        super().__init__(parameters)
        self.parameters = parameters
        self.sheet_row_count = parameters.get("row_count", 0)
        self.sheet_id = parameters.get("sheet_id")
        self.batch_size = parameters.get("batch_size")

    def stream_slices(self) -> Iterable[StreamSlice]:
        start_range = 2  # skip 1 row, as expected column (fields) names there

        while start_range <= self.sheet_row_count:
            end_range = start_range + self.batch_size
            logger.info(f"Fetching range {self.sheet_id}!{start_range}:{end_range}")
            yield StreamSlice(partition={"start_range": start_range, "end_range": end_range}, cursor_slice={})
            start_range = end_range + 1

# Spreadsheet Models

class SpreadsheetProperties(BaseModel):
    class Config:
        extra = Extra.allow

    title: Optional[str] = None


class SheetProperties(BaseModel):
    class Config:
        extra = Extra.allow

    title: Optional[str] = None


class CellData(BaseModel):
    class Config:
        extra = Extra.allow

    formattedValue: Optional[str] = None


class RowData(BaseModel):
    class Config:
        extra = Extra.allow

    values: Optional[List[CellData]] = None


class GridData(BaseModel):
    class Config:
        extra = Extra.allow

    rowData: Optional[List[RowData]] = None


class Sheet(BaseModel):
    class Config:
        extra = Extra.allow

    data: Optional[List[GridData]] = None
    properties: Optional[SheetProperties] = None


class Spreadsheet(BaseModel):
    class Config:
        extra = Extra.allow

    spreadsheetId: str
    sheets: List[Sheet]
    properties: Optional[SpreadsheetProperties] = None

# Spreadsheet Values Models

class ValueRange(BaseModel):
    class Config:
        extra = Extra.allow

    values: Optional[List[List[str]]] = None


class SpreadsheetValues(BaseModel):
    class Config:
        extra = Extra.allow

    spreadsheetId: str
    valueRanges: List[ValueRange]



def name_conversion(text: str) -> str:
    """
    convert name using a set of rules, for example: '1MyName' -> '_1_my_name'
    """
    text = unidecode.unidecode(text)

    tokens = []
    for m in TOKEN_PATTERN.finditer(text):
        if m.group("NoToken") is None:
            tokens.append(m.group(0))
        else:
            tokens.append("")

    if len(tokens) >= 3:
        tokens = tokens[:1] + [t for t in tokens[1:-1] if t] + tokens[-1:]

    if tokens and tokens[0].isdigit():
        tokens.insert(0, "")

    text = DEFAULT_SEPARATOR.join(tokens)
    text = text.lower()
    return text


def safe_name_conversion(text: str) -> str:
    if not text:
        return text
    new = name_conversion(text)
    if not new:
        raise Exception(f"initial string '{text}' converted to empty")
    return new


def exception_description_by_status_code(code: int, spreadsheet_id) -> str:
    if code in [status_codes.INTERNAL_SERVER_ERROR, status_codes.BAD_GATEWAY, status_codes.SERVICE_UNAVAILABLE]:
        return (
            "There was an issue with the Google Sheets API. This is usually a temporary issue from Google's side."
            " Please try again. If this issue persists, contact support"
        )
    if code == status_codes.FORBIDDEN:
        return (
            f"The authenticated Google Sheets user does not have permissions to view the spreadsheet with id {spreadsheet_id}. "
            "Please ensure the authenticated user has access to the Spreadsheet and reauthenticate. If the issue persists, contact support"
        )
    if code == status_codes.NOT_FOUND:
        return (
            f"The requested Google Sheets spreadsheet with id {spreadsheet_id} does not exist. "
            f"Please ensure the Spreadsheet Link you have set is valid and the spreadsheet exists. If the issue persists, contact support"
        )

    if code == status_codes.TOO_MANY_REQUESTS:
        return "Rate limit has been reached. Please try later or request a higher quota for your account."

    return ""
