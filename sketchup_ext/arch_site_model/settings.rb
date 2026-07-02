# 백엔드 URL 제공. 패키징 시 build_rbz.py 가 DEFAULT_BACKEND 한 줄에 URL을 박는다
# (런타임 입력·저장 없음). 개발 빌드=localhost, 배포 빌드=Cloud Run URL.

module ArchSiteModel
  module Settings
    # 백엔드 URL은 패키징 시 `build_rbz.py --backend-url <URL>` 이 아래 한 줄에 박는다
    # (개발 빌드=localhost, 배포 빌드=Cloud Run URL). 런타임 입력·저장 없음 → 혼동 방지.
    DEFAULT_BACKEND = "http://localhost:8000".freeze

    def self.backend_url
      DEFAULT_BACKEND.sub(%r{/+\z}, "") # 끝의 / 제거
    end
  end
end
