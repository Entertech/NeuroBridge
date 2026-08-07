#ifndef C1AF97F0_5653_4627_A4C0_531EB4CA8C2C
#define C1AF97F0_5653_4627_A4C0_531EB4CA8C2C
#include "TypeDefine.h"
#include "SessionCache.h"
#include "NumCpp.hpp"
#include "MeditationHandler.h"
#include "AffectionData.h"
#include "SimpleHandler.h"
namespace ac
{
    struct MeditationTemp
    {
        int index;                                                          // 计数
        double timeCum;                                                        // 累积运行时间
        vectord wearFlagStore;                                  // 佩戴标志暂存序列，用于判断脱落
        vectord eeglSplitData;                                              // 左通道脑电数据片段
        vectord eegrSplitData;                                              // 右通道脑电数据片段
        basic::dsp::EEGPower eegPower;                                      // 脑电能量，用于平滑
        double meditationDegreeTmp;                                         // 冥想度暂存值，用于信号质量不好时维持上一结果
        basic::affection::define::MeditationState meditationStateTmp;       // 冥想状态暂存值，用于信号质量不好时维持上一结果
        basic::affection::handler::AiMeditationHandlerTemp aiMeditationTmp; // 智能冥想度处理器缓存
        basic::affection::handler::MeditationHandlerTemp meditationTmp;     // 简单冥想度处理器缓存
        double preMeditation;                                               // 上一冥想度
        int lossTipsFlagCount;
        bool lossTipsCheckFlag;
        bool lossTipsReadyFlag;
        int backTipsFlagCount;
        bool backTipsCheckFlag;
        bool backTipsReadyFlag;
        vectord meditationRec;
        vectord meditationTipsRec;
    };

    struct MeditationTriggerRes
    {
        double meditation;     // 冥想程度
        double meditationTips; // 冥想状态提示
    };

    struct MeditationReportRes
    {
        double meditationAvg;      // 冥想度平均值
        vectord meditationRec;     // 冥想度全程记录
        vectord meditationTipsRec; // 冥想状态提示全程记录
        double flowPercent;        // 心流状态占比
        double flowDuration;       // 心流状态时长
        double flowLatency;        // 进入心流状态的用时
        int flowCombo;          // 心流状态最大连续时间
        double flowDepth;          // 心流状态最大深度
        int flowBackNum;        // 心流状态恢复提示次数
        int flowLossNum;        // 心流状态丢失提示次数
    };

    class MeditationComputing
    {
    public:
        MeditationTriggerRes trigger(basic::SessionCache &cache, vectord &eeglData, vectord &eegrData);
        MeditationReportRes report();
        int reportLength();
        MeditationComputing();
        ~MeditationComputing();

    private:
        MeditationTemp temp;
        vectord stateBoundary;
        void tempInit(bool isReset);
    };

}
#endif /* C1AF97F0_5653_4627_A4C0_531EB4CA8C2C */
