# .dae 가져오기 후처리 — 지형·도로·보도·수계 면 사이 모서리를 부드럽게(soft+smooth).
#
# SketchUp의 COLLADA 가져오기는 모서리를 절대 부드럽게 하지 않는다 — 2026-09-21 실험(SketchUp 2026,
# -RubyStartup 자동 가져오기): 곡면 지형 238개 모서리 중 soft 0, 정점 법선을 넣어도 0. 그래서
# 웹에서 받은 .dae 지형이 삼각형 선으로 새까맣게 보인다. 이 확장이 설치돼 있으면 가져오는 즉시
# 우리 .dae의 면 재질(collada.py _MATERIALS 이름)로 알아보고 정리한다. 건물은 건드리지 않는다.

module ArchSiteModel
  module ImportSoftener
    # src/output/collada.py 재질 이름과 같아야 한다(가져오면 이름 뒤에 번호가 붙을 수 있어 접두 비교).
    SURFACE_MATERIALS = %w[정사영상 지형 도로 보도 수계].freeze

    @done = {}
    @pending = false

    # 가져온 모델은 배치 도구로 놓이는 순간(또는 Ruby add_instance) 최상위 엔티티로 추가된다 —
    # 그때를 잡는다. (DefinitionsObserver#onComponentAdded는 가져오기에서 오지 않았다: 2026-09-21 실험)
    class TopEntitiesObserver < Sketchup::EntitiesObserver
      def onElementAdded(_entities, entity)
        ImportSoftener.schedule if entity.is_a?(Sketchup::ComponentInstance)
      end
    end

    class PlaceObserver < Sketchup::ModelObserver
      def onPlaceComponent(_instance)
        ImportSoftener.schedule
      end
    end

    class AppObs < Sketchup::AppObserver
      # SketchUp 시작 시 만드는 첫 모델에도 onNewModel/onOpenModel을 받는다(확장 로드가 그보다 먼저라서).
      def expectsStartupModelNotifications
        true
      end

      def onNewModel(model)
        ImportSoftener.attach(model)
      end

      def onOpenModel(model)
        ImportSoftener.attach(model)
      end
    end

    @attached = {}
    @runs = 0
    class << self
      attr_reader :runs, :last_result # 자동 정리가 실제로 돈 횟수·마지막 결과(검증용)
    end

    def self.attach(model)
      return unless model
      return if @attached[model.guid] # 같은 모델에 두 번 붙이지 않는다
      @attached[model.guid] = true
      model.entities.add_observer(TopEntitiesObserver.new)
      model.add_observer(PlaceObserver.new)
    rescue StandardError
      nil
    end

    # 가져오기는 정의를 수백 개 연달아 만든다 — 끝난 뒤 한 번만 돌도록 타이머로 모은다.
    def self.schedule
      return if @pending
      @pending = true
      UI.start_timer(0.5, false) do
        @pending = false
        @runs += 1
        @last_result = soften_all(Sketchup.active_model)
      end
    end

    def self.surface?(face)
      m = face.material
      m && SURFACE_MATERIALS.any? { |n| m.name.start_with?(n) }
    end

    # 반환: 부드럽게 만든 모서리 수
    def self.soften_all(model)
      return 0 unless model
      n = 0
      targets = model.definitions.select do |d|
        next false if @done[d.persistent_id]
        f = d.entities.grep(Sketchup::Face).first
        f && surface?(f)
      end
      return 0 if targets.empty?
      model.start_operation("대지모델 모서리 정리", true, false, true) # 가져오기 undo에 합침
      targets.each do |d|
        d.entities.grep(Sketchup::Edge).each do |e|
          next unless e.faces.length == 2 # 외곽 테두리는 남긴다
          e.soft = true
          e.smooth = true
          n += 1
        end
        @done[d.persistent_id] = true
      end
      model.commit_operation
      puts "[arch-site-model] .dae 면 모서리 #{n}개 정리"
      n
    rescue StandardError => e
      model.abort_operation rescue nil
      puts "[arch-site-model] 모서리 정리 실패: #{e.message}"
      "error: #{e.class}: #{e.message}"
    end

    unless file_loaded?(__FILE__)
      attach(Sketchup.active_model)
      Sketchup.add_observer(AppObs.new)
      file_loaded(__FILE__)
    end
  end
end
