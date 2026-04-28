from typing import Union

from pydantic import BaseModel

from saver_backend.entities.enums import ContentTypeEnum
from saver_backend.services.downloaders.schema import (
    AudioDTO,
    PhotoDTO,
    PhotoListDTO,
    VideoDTO,
    VideoTheatreDTO,
)

CacheableDTO = Union[VideoDTO, PhotoDTO, AudioDTO, PhotoListDTO, VideoTheatreDTO]

# Mapping: Enum -> DTO Type
# Used for deserializing content from PostgreSQL
CONTENT_TYPE_TO_DTO_MAP: dict[ContentTypeEnum, type[CacheableDTO]] = {
    ContentTypeEnum.VIDEO: VideoDTO,
    ContentTypeEnum.PHOTO: PhotoDTO,
    ContentTypeEnum.AUDIO: AudioDTO,
    ContentTypeEnum.PHOTO_LIST: PhotoListDTO,
    ContentTypeEnum.FILM_DICT: VideoTheatreDTO,
}

# Mapping: DTO Type -> Enum
# Using for serializing content to save in PostgreSQL
DTO_TO_CONTENT_TYPE_MAP: dict[type[BaseModel], ContentTypeEnum] = {
    VideoDTO: ContentTypeEnum.VIDEO,
    PhotoDTO: ContentTypeEnum.PHOTO,
    AudioDTO: ContentTypeEnum.AUDIO,
    PhotoListDTO: ContentTypeEnum.PHOTO_LIST,
    VideoTheatreDTO: ContentTypeEnum.FILM_DICT,
}
