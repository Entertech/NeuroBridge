#ifndef B323A715_6239_4A1D_930D_BFE4460898F8
#define B323A715_6239_4A1D_930D_BFE4460898F8
#ifndef AC_SLEEP_HEADER_FILE_GUARD 
#define AC_SLEEP_HEADER_FILE_GUARD
#include "TypeDefine.h"
#include "SessionCache.h"
#include "NumCpp.hpp"
#include "SleepHandler.h"
namespace ac {
    struct SleepTemp 
    {
        int index; //计数
        double time_cum; //累积运行时间
        vectord eegSplitData; //脑电数据片段
        basic::dsp::EEGPower eegPower; //脑电能量，用于平滑
        double sleepDegreeTmp; //睡眠程度暂存值，用于信号质量不好时维持上一结果
        int sleepStateTmp; //睡眠状态暂存值，用于信号质量不好时维持上一结果
        int sleepStageTmp;
        double movementAmpTmp;
        double arousalPowerTmp;
        basic::affection::handler::SleepHandlerTemp sleepTmp; //
        vectori wearRec;
        vectord sleepDegreeRec;
        vectori sleepStateRec;
        vectori sleepStageRec;
        vectord sleepEEGAlphaRec;
        vectord sleepEEGBetaRec;
        vectord sleepEEGThetaRec;
        vectord sleepEEGDeltaRec;
        vectord sleepEEGGammaRec;
        vectord sleepEEGHighBetaRec;
        vectord sleepEEGLowBetaRec;
        vectord sleepEEGSpindleRec;
        vectori sleepEEGQualityRec;
        vectori sleepEEGMovementRec;
        vectori sleepEEGArousalRec;
    };

    struct SleepTriggerRes
    {
        double sleepDegree;
        int sleepState;
        int sleepStage;
        double sleepSpindle;
    };

    struct SleepReportRes
    {
        vectord sleepCurve;
        vectori sleepStage;
        int sleepPoint;
        int sleepLatency;
        int awakeDuration;
        int remDuration;
        int lightDuration;
        int deepDuration;
        vectord eegAlphaCurve;
        vectord eegBetaCurve;
        vectord eegThetaCurve;
        vectord eegDeltaCurve;
        vectord eegGammaCurve;
        vectori eegQualityRec;
        vectord eegHighBetaDBCurve;
        vectord eegLowBetaDBCurve;
        int movementCount;
        int arousalCount;
        double disturbTolerance;
        vectori movementRec;
        vectori arousalRec;
        vectori spindleRec;
    };

    class SleepComputing
    {
    public:
        /// 实时触发
        /// \param cache 会话内部缓存
        /// \param eegData 解析后的左通道脑电数据
        /// \return 睡眠程度，睡眠状态
        SleepTriggerRes trigger(basic::SessionCache &cache, vectord &eegData);
        SleepReportRes report();
        int reportLength();
        SleepComputing();
        ~SleepComputing();
    private:
        SleepTemp temp;
    };
}
#endif


#endif /* B323A715_6239_4A1D_930D_BFE4460898F8 */
