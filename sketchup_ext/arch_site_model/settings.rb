# 확장 설정 저장 (백엔드 URL). SketchUp 레지스트리(read/write_default)에 영구 저장.

module ArchSiteModel
  module Settings
    SECTION = "arch_site_model".freeze
    # 개발 기본값 = localhost. 배포용 .rbz 는 `build_rbz.py --backend-url <URL>` 이
    # 패키징 시 아래 한 줄을 그 URL로 치환한다(소스는 localhost 유지) → 팀원은 URL 입력 불필요.
    DEFAULT_BACKEND = "http://localhost:8000".freeze

    def self.backend_url
      url = Sketchup.read_default(SECTION, "backend_url", DEFAULT_BACKEND)
      url = DEFAULT_BACKEND if url.nil? || url.strip.empty?
      url.sub(%r{/+\z}, "") # 끝의 / 제거
    end

    def self.backend_url=(value)
      return if value.nil?
      v = value.to_s.strip
      Sketchup.write_default(SECTION, "backend_url", v) unless v.empty?
    end
  end
end
