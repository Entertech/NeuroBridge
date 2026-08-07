#include "Meditation.h"
#include "Device.h"
#include "Data.h"
#include "Pretreat.h"
#include "Feature.h"
#include "MathTool.h"
#include "Basic.hpp"

using namespace basic;
using namespace affection;

namespace ac
{
    MeditationComputing::MeditationComputing()
    {
        tempInit(false);
        stateBoundary.push_back(100.0 / 3.0);
        stateBoundary.push_back(200.0 / 3.0);
    }
    MeditationComputing::~MeditationComputing()
    {
    }

    void MeditationComputing::tempInit(bool isReset)
    {

        temp.eeglSplitData.clear();
        temp.eegrSplitData.clear();
        temp.eegPower = dsp::EEGPower();
        temp.meditationDegreeTmp = 0.;
        temp.meditationStateTmp = define::MeditationState::ACTIVE;
        temp.aiMeditationTmp.meditationState = define::MeditationState::ACTIVE;
        temp.aiMeditationTmp.meditationStateStore.resize(7);
        for (size_t i = 0; i < temp.aiMeditationTmp.meditationStateStore.size(); i++)
        {
            temp.aiMeditationTmp.meditationStateStore[i] = define::MeditationState::ACTIVE;
        }
        temp.aiMeditationTmp.meditationDegreeStore.clear();

        temp.meditationTmp.weight = 0.3;
        nc::NdArray<double> features1 = {};
        nc::NdArray<double> features2 = {};
        nc::NdArray<double> features3 = {};
        nc::NdArray<double> features4 = {};
        temp.meditationTmp.featuresStore.clear();
        temp.meditationTmp.featuresStore.push_back(features1);
        temp.meditationTmp.featuresStore.push_back(features2);
        temp.meditationTmp.featuresStore.push_back(features3);
        temp.meditationTmp.featuresStore.push_back(features4);
        temp.meditationTmp.rulers = params::meditationRuler;
        temp.meditationTmp.meditationStore = {};

        temp.preMeditation = 0;
        temp.lossTipsFlagCount = 0;
        temp.lossTipsCheckFlag = false;
        temp.lossTipsReadyFlag = false;
        temp.backTipsFlagCount = 0;
        temp.backTipsCheckFlag = false;
        temp.backTipsReadyFlag = false;

        if (!isReset) {
            temp.index = 0;
            temp.timeCum = 0.;
            temp.wearFlagStore.resize(15);
            std::fill(temp.wearFlagStore.begin(), temp.wearFlagStore.end(), 0.);
            temp.meditationRec.clear();
            temp.meditationTipsRec.clear();
        }

    }

    /// @brief 情感计算方法
    /// @param cache 会话内部缓存
    /// @param eeglData 解析后的左通道脑电数据
    /// @param eegrData 解析后的右通道脑电数据
    /// return  冥想度值，冥想状态提示
    MeditationTriggerRes MeditationComputing::trigger(basic::SessionCache &cache, vectord &eeglData, vectord &eegrData)
    {
        // 初始化
        MeditationTriggerRes res;
        auto deviceInfo = new dsp::DeviceInfoFtV1();
        auto stepSec = eeglData.size() / deviceInfo->eegFs();

        // 读取外部缓存
        auto eegQuality = cache.eegQuality;
        auto eegPower = cache.eegPower;
        auto eeglPower = cache.eeglPower;
        auto eegrPower = cache.eegrPower;

        // 有效性判断
        std::copy(temp.wearFlagStore.begin() + 1, temp.wearFlagStore.end(), temp.wearFlagStore.begin());
        if (eegQuality != dsp::EEGQuality::NONE)
            temp.wearFlagStore[14] = 1;
        else
            temp.wearFlagStore[14] = 0;

        auto wearFlagSum = accumulate(temp.wearFlagStore.begin(), temp.wearFlagStore.end(), 0.);
        if (wearFlagSum == 0.)
            tempInit(true);

        // 执行算法
        // 数据拼接
        temp.eeglSplitData.insert(temp.eeglSplitData.end(), eeglData.begin(), eeglData.end());
        temp.eegrSplitData.insert(temp.eegrSplitData.end(), eegrData.begin(), eegrData.end());

        // 冥想度计算
        if (temp.eeglSplitData.size() < size_t(12 * deviceInfo->eegFs()) || temp.eegrSplitData.size() < size_t(12 * deviceInfo->eegFs()))
        {
            res.meditation = 0;
            res.meditationTips = 0;
        }
        else
        {
            // 数据裁剪
            auto eegSize = size_t(12 * deviceInfo->eegFs());
            std::copy(temp.eeglSplitData.end() - eegSize, temp.eeglSplitData.end(), temp.eeglSplitData.begin());
            std::copy(temp.eegrSplitData.end() - eegSize, temp.eegrSplitData.end(), temp.eegrSplitData.begin());
            temp.eeglSplitData.resize(eegSize);
            temp.eegrSplitData.resize(eegSize);

            // 特征提取
            auto eegFeature = dsp::meditationEEGFeatures(
                temp.eeglSplitData,
                temp.eegrSplitData,
                deviceInfo);

            // 计算冥想度
            // ---专家模型
            auto meditationSp = handler::meditationHandler(
                eegPower.alphaNorm(),
                eegPower.thetaNorm(),
                eegPower.gammaNorm(),
                eeglPower.alphaNorm(),
                eegrPower.alphaNorm(),
                temp.meditationTmp,
                10,
                180,
                30,
                0.7,
                30.0,
                1.0);

            // ---机器学习模型
            auto meditationAi = handler::aiMeditationHandler(
                eegFeature,
                eegQuality,
                temp.timeCum,
                temp.aiMeditationTmp,
                10);

            
            // ---融合模型
            if (meditationSp > 0 && meditationAi.meditationDegree > 0)
            {
                nc::NdArray<double> degree = {meditationAi.meditationDegree};
                nc::NdArray<double> xp = {0, 20, 40, 70, 100};
                nc::NdArray<double> fp = {0.3, 0.4, 0.6, 0.7, 0.7};
                auto aiWeight = nc::interp(degree, xp, fp).item();
                res.meditation = meditationAi.meditationDegree * aiWeight + meditationSp * (1 - aiWeight);
            }
            else
                res.meditation = 0;

            // 计算冥想提示
            double meditationTips = 0.0;
            if (res.meditation >= 50)
                temp.lossTipsCheckFlag = true;
            if (res.meditation <= stateBoundary[0] && stateBoundary[0] < temp.preMeditation && temp.lossTipsCheckFlag)
                temp.lossTipsReadyFlag = true;
            if (res.meditation <= stateBoundary[0] && temp.lossTipsReadyFlag)
                temp.lossTipsFlagCount += 1;
            else
            {
                temp.lossTipsFlagCount = 0;
                temp.lossTipsReadyFlag = false;
            }
            if (temp.lossTipsFlagCount >= 5)
            {
                meditationTips = 2;
                temp.lossTipsCheckFlag = false;
                temp.lossTipsReadyFlag = false;
                temp.lossTipsFlagCount = 0;
            }

            if (res.meditation <= 50)
                temp.backTipsCheckFlag = true;
            if (res.meditation >= stateBoundary[1] && stateBoundary[1] > temp.preMeditation && temp.backTipsCheckFlag)
                temp.backTipsReadyFlag = true;
            if (res.meditation >= stateBoundary[1] && temp.backTipsReadyFlag)
                temp.backTipsFlagCount += 1;
            else
            {
                temp.backTipsFlagCount = 0;
                temp.backTipsReadyFlag = false;
            }
            if (temp.backTipsFlagCount >= 5)
            {
                meditationTips = 1;
                temp.backTipsCheckFlag = false;
                temp.backTipsReadyFlag = false;
                temp.backTipsFlagCount = 0;
            }
            res.meditationTips = meditationTips;
            temp.preMeditation = res.meditation;
            temp.timeCum += stepSec;
        }
        //更新内部缓存（模拟输出结果至数据库）
        temp.index += 1;
        temp.meditationRec.push_back(res.meditation);
        temp.meditationTipsRec.push_back(res.meditationTips);
        delete deviceInfo;
        //输出结果
        return res;
    }


    /// @brief 报表计算
    /// @return 
    MeditationReportRes MeditationComputing::report()
    {
        MeditationReportRes res;
        res.meditationAvg = 0;
        if (temp.meditationRec.size() < 1)
            return res;

        // 读取内部缓存
        vectord meditationRec(temp.meditationRec);
        vectord meditationTipRec(temp.meditationTipsRec);

        // 执行算法
        // ---入定状态百分比
        vectord validMeditationRec;
        int largerThanBoudaryCount = 0;
        int largerThanBoudaryFirstIndex = 0;
        int flowFirstIndex = 0;
        int flowComboCount = 0;
        int largestComboCount = 0;
        for (auto &e : meditationRec)
        {
            
            if (e > 0)
                validMeditationRec.push_back(e);
            if (e >= stateBoundary[1])
            {
                flowComboCount ++;
                largerThanBoudaryCount += 1;
                if (largerThanBoudaryFirstIndex == 0)
                    largerThanBoudaryFirstIndex = flowFirstIndex;
            } else {
                if (flowComboCount > largestComboCount)
                {
                    largestComboCount = flowComboCount;
                    flowComboCount = 0;
                }

            }
            flowFirstIndex ++;
        }
        if (flowComboCount > largestComboCount)
        {
            largestComboCount = flowComboCount;
            flowComboCount = 0;
        }
        double flowPercent = 0.;
        if (validMeditationRec.size() > 0)
        {
            flowPercent = double(largerThanBoudaryCount)/double(validMeditationRec.size())*100.;
        }

        // ---入定状态时长
        auto flowDuration = largerThanBoudaryCount * 0.6;

        // ---入定用时
        double flowLatency = (largerThanBoudaryFirstIndex+1)*0.6;

        // ---最长入定持续时长
        double flowCombo = largestComboCount * 0.6;
        // ---入定深度
        auto maxPoint = std::max_element(meditationRec.begin(), meditationRec.end());
        double flowDepth = *maxPoint;

        // ---冥想状态提示数
        int flowBackNum = std::count(meditationTipRec.begin(), meditationTipRec.end(), 1);
        int flowLossNum = std::count(meditationTipRec.begin(), meditationTipRec.end(), 2);
        
        // ---冥想曲线平滑
        auto maxLen = meditationRec.size() * 0.01 > 8.0 ? int(meditationRec.size() * 0.01) : 8;
        auto halfSmoothLen = maxLen > meditationRec.size() ? meditationRec.size() : maxLen;
        auto meditationCurve = mathtool::smoothCurveCal(meditationRec, halfSmoothLen);

        // 结果输出
        res.meditationAvg = mathtool::mean(temp.meditationRec);
        res.meditationRec = meditationCurve;
        res.meditationTipsRec = meditationTipRec;
        res.flowPercent = flowPercent;
        res.flowDuration = flowDuration;
        res.flowLatency = flowLatency;
        res.flowCombo = int(flowCombo);
        res.flowDepth = flowDepth;
        res.flowBackNum = flowBackNum;
        res.flowLossNum = flowLossNum;

        return res;
    }
}