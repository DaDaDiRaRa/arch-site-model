# 백엔드 /api/generate 호출. Sketchup::Http(비동기)로 UI를 막지 않는다.
# 응답의 geometry(로컬 미터) + warnings 를 콜백으로 넘긴다.

require "json"

module ArchSiteModel
  module ApiClient
    # Sketchup::Http::Request 는 참조를 유지하지 않으면 비동기 콜백이 도착하기 전에
    # GC 로 수거돼 요청이 조용히 실패한다. 특히 **앞서 만든 모델을 삭제**하면 대량 해제로
    # GC 가 강하게 발동해, 붙잡히지 않은 요청 객체가 전송 도중 수거된다 — "한 번은 되고
    # 삭제 후 다시 하면 에러, SketchUp 재시작하면 복구"의 정확한 원인. 콜백이 끝날 때까지
    # 모듈 수준 배열에 붙잡아 두고(=GC 대상에서 제외), 끝나면 놓아준다.
    @pending = []

    def self.retain(request)
      @pending << request
      request
    end

    def self.release(request)
      @pending.delete(request)
    rescue StandardError
      nil
    end

    # base_url: 예 "http://localhost:8000"
    # params: {"address"=>..., "radius_m"=>..., "terrain"=>true/false}
    # yield: 결과 해시 { geometry:, warnings:, error: }
    def self.generate(base_url, params, &callback)
      url = "#{base_url}/api/generate"
      body = {
        "address"    => params["address"].to_s,
        "radius_m"   => (params["radius_m"] || 250).to_i,
        # orthophoto=true면 백엔드가 정사영상 PNG 생성 → 확장이 다운로드해 지형에 드레이프(B2).
        # roads=true면 지형·도로·보도를 통합 삼각화해 geometry.roads/sidewalks/lanes 반환(Phase R).
        "layers"     => {
          "buildings"  => true,
          "terrain"    => params["terrain"] != false,
          "orthophoto" => params["orthophoto"] == true,
          "roads"      => params["roads"] == true,
          "water"      => params["water"] == true,
          "qa"         => params["qa"] == true,
        },
        "outputs"    => ["skp"],  # .3dm 불필요 — geometry + 정사영상 URL만 받음
      }

      request = Sketchup::Http::Request.new(url, Sketchup::Http::POST)
      request.headers = { "Content-Type" => "application/json" }
      request.body = JSON.generate(body)

      retain(request)
      request.start do |_req, response|
        begin
          callback.call(parse_response(response))
        ensure
          release(request)
        end
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
      # 정사영상: extent(geometry) + 다운로드 URL(files.ortho_png)이 모두 있으면 제공.
      ortho = nil
      ext = geom["ortho_extent_m"]
      ourl = (data["files"] || {})["ortho_png"]
      ortho = { extent: ext, url: ourl } if ext && ourl
      { geometry: geom, warnings: data["warnings"] || [], ortho: ortho, qa: data["qa"] }
    rescue JSON::ParserError => e
      { error: "응답 파싱 실패: #{e.message}" }
    rescue StandardError => e
      { error: "응답 처리 오류: #{e.message}" }
    end

    # 바이너리(정사영상 PNG 등) 다운로드. yield: 바이트 문자열 또는 nil(실패).
    def self.download_binary(url, &callback)
      request = Sketchup::Http::Request.new(url, Sketchup::Http::GET)
      retain(request)
      request.start do |_req, response|
        begin
          callback.call(response.status_code == 200 ? response.body : nil)
        ensure
          release(request)
        end
      end
    rescue StandardError
      callback.call(nil)
    end

    # --- 대반경 순차조립 (타일) -------------------------------------------

    # 계획: 주소 → 고정 offset + 타일 목록. yield {plan:} 또는 {error:}.
    def self.tile_plan(base_url, params, &callback)
      url = "#{base_url}/api/tile_plan"
      body = {
        "address"     => params["address"].to_s,
        "radius_m"    => (params["radius_m"] || 1000).to_i,
        "tile_size_m" => (params["tile_size_m"] || 250).to_f,
      }
      request = Sketchup::Http::Request.new(url, Sketchup::Http::POST)
      request.headers = { "Content-Type" => "application/json" }
      request.body = JSON.generate(body)
      retain(request)
      request.start do |_req, response|
        begin
          callback.call(parse_plan(response))
        ensure
          release(request)
        end
      end
    rescue StandardError => e
      callback.call({ error: "계획 요청 실패: #{e.message}" })
    end

    def self.parse_plan(response)
      code = response.status_code
      unless code == 200
        detail = safe_detail(response.body)
        return { error: "계획 실패 (HTTP #{code})#{detail ? ": #{detail}" : ""}" }
      end
      data = JSON.parse(response.body)
      return { error: "계획 응답에 tiles가 없습니다." } if data["tiles"].nil?
      { plan: data }
    rescue JSON::ParserError => e
      { error: "계획 파싱 실패: #{e.message}" }
    end

    # 타일 1개 geometry. tile: {"bbox_4326","bbox_5186","origin_offset","layers"}.
    # yield {geometry:, solids:, terrain_triangles:} 또는 {error:}.
    def self.generate_tile(base_url, tile, &callback)
      url = "#{base_url}/api/generate_tile"
      request = Sketchup::Http::Request.new(url, Sketchup::Http::POST)
      request.headers = { "Content-Type" => "application/json" }
      request.body = JSON.generate(tile)
      retain(request)
      request.start do |_req, response|
        begin
          callback.call(parse_tile(response))
        ensure
          release(request)
        end
      end
    rescue StandardError => e
      callback.call({ error: "타일 요청 실패: #{e.message}" })
    end

    def self.parse_tile(response)
      code = response.status_code
      unless code == 200
        detail = safe_detail(response.body)
        return { error: "타일 실패 (HTTP #{code})#{detail ? ": #{detail}" : ""}" }
      end
      data = JSON.parse(response.body)
      geom = data["geometry"]
      return { error: "타일 응답에 geometry가 없습니다." } if geom.nil?
      {
        geometry: geom,
        solids: data["solids"] || 0,
        terrain_triangles: data["terrain_triangles"] || 0,
        ortho: data["ortho"],  # {extent_local_m, image_b64}|nil — 타일별 정사영상
      }
    rescue JSON::ParserError => e
      { error: "타일 파싱 실패: #{e.message}" }
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
