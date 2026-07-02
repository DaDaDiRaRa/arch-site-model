# 백엔드 /api/generate 호출. Sketchup::Http(비동기)로 UI를 막지 않는다.
# 응답의 geometry(로컬 미터) + warnings 를 콜백으로 넘긴다.

require "json"

module ArchSiteModel
  module ApiClient
    # base_url: 예 "http://localhost:8000"
    # params: {"address"=>..., "radius_m"=>..., "terrain"=>true/false}
    # yield: 결과 해시 { geometry:, warnings:, error: }
    def self.generate(base_url, params, &callback)
      url = "#{base_url}/api/generate"
      body = {
        "address"    => params["address"].to_s,
        "radius_m"   => (params["radius_m"] || 250).to_i,
        # B1: 텍스처 없음 → orthophoto=false, .3dm 불필요 → outputs=skp(경량).
        "layers"     => { "buildings" => true, "terrain" => params["terrain"] != false, "orthophoto" => false },
        "outputs"    => ["skp"],
      }

      request = Sketchup::Http::Request.new(url, Sketchup::Http::POST)
      request.headers = { "Content-Type" => "application/json" }
      request.body = JSON.generate(body)

      request.start do |_req, response|
        callback.call(parse_response(response))
      end
    rescue StandardError => e
      callback.call({ error: "요청 생성 실패: #{e.message}" })
    end

    def self.parse_response(response)
      code = response.status_code
      unless code == 200
        detail = safe_detail(response.body)
        return { error: "생성 실패 (HTTP #{code})#{detail ? ": #{detail}" : ""}" }
      end
      data = JSON.parse(response.body)
      geom = data["geometry"]
      return { error: "응답에 geometry가 없습니다. 백엔드 버전을 확인하세요." } if geom.nil?
      { geometry: geom, warnings: data["warnings"] || [] }
    rescue JSON::ParserError => e
      { error: "응답 파싱 실패: #{e.message}" }
    rescue StandardError => e
      { error: "응답 처리 오류: #{e.message}" }
    end

    # 에러 응답 본문에서 detail 메시지만 뽑아본다(있으면).
    def self.safe_detail(body)
      d = JSON.parse(body)
      d.is_a?(Hash) ? d["detail"] : nil
    rescue StandardError
      nil
    end
  end
end
