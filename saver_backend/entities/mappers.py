from typing import Union

from pydantic import BaseModel

from saver_backend.entities.enums import ContentTypeEnum
from saver_backend.services.downloaders.schema import (
    AudioDTO,
    PhotoDTO,
    PhotoListDTO,
    VideoDTO,
)

CacheableDTO = Union[VideoDTO, PhotoDTO, AudioDTO, PhotoListDTO]

# Маппинг: Enum -> Тип DTO
# Используется для десериализации данных из БД
CONTENT_TYPE_TO_DTO_MAP: dict[ContentTypeEnum, type[CacheableDTO]] = {
    ContentTypeEnum.VIDEO: VideoDTO,
    ContentTypeEnum.PHOTO: PhotoDTO,
    ContentTypeEnum.AUDIO: AudioDTO,
    ContentTypeEnum.PHOTO_LIST: PhotoListDTO,
}

# Маппинг: Тип DTO -> Enum
# Используется для определения типа контента при сохранении в БД
DTO_TO_CONTENT_TYPE_MAP: dict[type[BaseModel], ContentTypeEnum] = {
    VideoDTO: ContentTypeEnum.VIDEO,
    PhotoDTO: ContentTypeEnum.PHOTO,
    AudioDTO: ContentTypeEnum.AUDIO,
    PhotoListDTO: ContentTypeEnum.PHOTO_LIST,
}
